from collections import OrderedDict
from contextlib import contextmanager

from django.utils.tree import Node

from .compat import convert_build_filter_to_add_q_kwargs, nullcontext
from .exceptions import QueryablePropertyError
from .utils.internal import InjectableMixin, NodeChecker, QueryPath, resolve_queryable_property

QUERYING_PROPERTIES_MARKER = '__querying_properties__'


class AggregatePropertyChecker(NodeChecker):
    """
    A specialized node checker that checks whether or not a node contains a
    reference to an aggregate property for the purposes of determining whether
    or not a HAVING clause is required.
    """

    def __init__(self):
        super().__init__(self.is_aggregate_property)

    def is_aggregate_property(self, item, model, ignored_refs=frozenset()):
        """
        Check if the given node item or its subnodes contain a reference to an
        aggregate property.

        :param (str, object) item: The node item consisting of path and value.
        :param model: The model class the corresponding query is performed for.
        :param ignored_refs: Queryable property references that should not be
                             checked.
        :type ignored_refs: frozenset[queryable_properties.utils.internal.QueryablePropertyReference]
        :return:
        """
        property_ref, lookups = resolve_queryable_property(model, QueryPath(item[0]))
        if not property_ref or property_ref in ignored_refs:
            return False
        if property_ref.property.filter_requires_annotation:
            if property_ref.get_annotation().contains_aggregate:
                return True
            ignored_refs = ignored_refs.union((property_ref,))
        # Also check the Q object returned by the property's get_filter method
        # as it may contain references to other properties that may add
        # aggregation-based annotations.
        return self.check_leaves(property_ref.get_filter(lookups, item[1]), model=model, ignored_refs=ignored_refs)


aggregate_property_checker = AggregatePropertyChecker()


class QueryablePropertiesCompilerMixin(InjectableMixin):
    """
    A mixin for :class:`django.db.models.sql.compiler.SQLCompiler` objects that
    extends the original Django objects to inject the
    ``QUERYING_PROPERTIES_MARKER``.
    """

    def setup_query(self, *args, **kwargs):
        super().setup_query(*args, **kwargs)
        # Add the marker to the column map while ensuring that it's the first
        # entry.
        annotation_col_map = OrderedDict()
        annotation_col_map[QUERYING_PROPERTIES_MARKER] = -1
        annotation_col_map.update(self.annotation_col_map)
        self.annotation_col_map = annotation_col_map

    def results_iter(self, *args, **kwargs):
        for row in super().results_iter(*args, **kwargs):
            # Add the fixed value for the fake querying properties marker
            # annotation to each row. The value can simply be appended since 
            # -1 can be specified as the index in the annotation_col_map. 
            yield row + row.__class__((True,))


class QueryablePropertiesQueryMixin(InjectableMixin):
    """
    A mixin for :class:`django.db.models.sql.Query` and
    :class:`django.db.models.sql.Raw Query` objects that extends the original
    Django objects to deal with queryable properties, e.g. managing used
    properties or automatically adding required properties as annotations.
    """

    def __iter__(self):  # Raw queries
        # See QueryablePropertiesCompilerMixin.results_iter, but for raw
        # queries. The marker can simply be added as the first value as fields
        # are not strictly grouped like in regular queries.
        for row in super().__iter__():
            if self._use_querying_properties_marker:
                row = row.__class__((True,)) + row
            yield row

    def init_injected_attrs(self):
        # Stores references to queryable properties used as annotations in this
        # query.
        self._queryable_property_annotations = set()
        # A stack for queryable properties who are currently being annotated.
        # Required to correctly resolve dependencies and perform annotations.
        self._queryable_property_stack = []
        # Determines whether to inject the QUERYING_PROPERTIES_MARKER.
        self._use_querying_properties_marker = False

    @contextmanager
    def _add_queryable_property_annotation(self, property_ref, full_group_by, select=False):
        """
        A context manager that adds a queryable property annotation to this
        query and performs management tasks around the annotation (stores the
        information if the queryable property annotation should be selected
        and populates the queryable property stack correctly). The context
        manager yields the actual resolved and applied annotation while the
        stack is still populated.

        :param property_ref: A reference containing the queryable property
                             to annotate.
        :type property_ref: queryable_properties.utils.internal.QueryablePropertyReference
        :param bool full_group_by: Signals whether to use all fields of the
                                   query for the GROUP BY clause when dealing
                                   with an aggregate-based annotation or not.
        :param bool select: Signals whether the annotation should be selected
                            or not.
        """
        if property_ref in self._queryable_property_stack:
            raise QueryablePropertyError('Queryable property "{}" has a circular dependency and requires itself.'
                                         .format(property_ref.property))

        annotation_name = str(property_ref.full_path)
        annotation_mask = set(self.annotations if self.annotation_select_mask is None else self.annotation_select_mask)
        self._queryable_property_stack.append(property_ref)
        try:
            if property_ref not in self._queryable_property_annotations:
                self.add_annotation(property_ref.get_annotation(), alias=annotation_name)
                if not select:
                    self.set_annotation_mask(annotation_mask)
                self._queryable_property_annotations.add(property_ref)
            elif select and self.annotation_select_mask is not None:
                self.set_annotation_mask(annotation_mask.union((annotation_name,)))
            annotation = self.annotations[annotation_name]
            yield annotation
        finally:
            self._queryable_property_stack.pop()

        # Perform the required GROUP BY setup if the annotation contained
        # aggregates, which is normally done by QuerySet.annotate.
        if annotation.contains_aggregate:
            if full_group_by:
                # In recent Django versions, a full GROUP BY can be achieved by
                # simply setting group_by to True.
                self.group_by = True
            else:
                if full_group_by and self.group_by is None:  # pragma: no cover
                    # In old versions, the fields must be added to the selected
                    # fields manually and set_group_by must be called after.
                    opts = self.model._meta
                    self.add_fields([f.attname for f in getattr(opts, 'concrete_fields', opts.fields)], False)
                self.set_group_by()

    def _auto_annotate(self, query_path, full_group_by=None):
        """
        Try to resolve the given path into a queryable property and annotate
        the property as a non-selected property (if the property wasn't added
        as an annotation already). Do nothing if the path does not match a
        queryable property.

        :param QueryPath query_path: The query path to resolve.
        :param bool | None full_group_by: Optional override to indicate whether
                                          or not all fields must be contained
                                          in a GROUP BY clause for aggregate
                                          annotations. If not set, it will be
                                          determined from the state of this
                                          query.
        :return: The resolved annotation or None if the path couldn't be
                 resolved.
        """
        property_ref = resolve_queryable_property(self.model, query_path)[0]
        if not property_ref:
            return None
        if full_group_by is None:
            full_group_by = False
        with self._add_queryable_property_annotation(property_ref, full_group_by) as annotation:
            return annotation

    def _postprocess_clone(self, clone):
        """
        Postprocess a query that was the result of cloning this query. This
        ensures that the cloned query also uses this mixin and that the
        queryable property attributes are initialized correctly.

        :param django.db.models.sql.Query clone: The cloned query.
        :return: The postprocessed cloned query.
        :rtype: django.db.models.sql.Query
        """
        QueryablePropertiesQueryMixin.inject_into_object(clone)
        clone.init_injected_attrs()
        clone._queryable_property_annotations.update(self._queryable_property_annotations)
        return clone

    def add_aggregate(self, aggregate, model=None, alias=None, is_summary=False):  # pragma: no cover
        # This method is called in older versions to add an aggregate, which
        # may be based on a queryable property annotation, which in turn must
        # be auto-annotated here.
        query_path = QueryPath(aggregate.lookup)
        if self._queryable_property_stack:
            query_path = self._queryable_property_stack[-1].relation_path + query_path
        property_annotation = self._auto_annotate(query_path)
        if property_annotation:
            # If it is based on a queryable property annotation, annotating the
            # current aggregate cannot be delegated to Django as it couldn't
            # deal with annotations containing the lookup separator.
            aggregate.add_to_query(self, alias, str(query_path), property_annotation, is_summary)
        else:
            # The overridden method also allows to set a default value for the
            # model parameter, which will be missing if add_annotation calls are
            # redirected to add_aggregate for older Django versions.
            model = model or self.model
            super().add_aggregate(aggregate, model, alias, is_summary)
        if self.annotation_select_mask is not None:
            self.set_annotation_mask(self.annotation_select_mask.union((alias,)))

    def add_ordering(self, *ordering, **kwargs):
        for field_name in ordering:
            # Ordering by a queryable property via simple string values
            # requires auto-annotating here, while a queryable property used
            # in a complex ordering expression is resolved through other
            # overridden methods.
            if isinstance(field_name, str) and field_name != '?':
                if field_name.startswith('-'):
                    field_name = field_name[1:]
                self._auto_annotate(QueryPath(field_name))
        return super().add_ordering(*ordering, **kwargs)

    @property
    def aggregate_select(self):  # pragma: no cover
        select = original = super().aggregate_select
        if self._use_querying_properties_marker:
            # Since old Django versions don't offer the annotation_col_map on
            # compilers, but read the annotations directly from the query, the
            # querying properties marker has to be injected here. The value for
            # the annotation will be provided via the compiler mixin.
            select = OrderedDict()
            select[QUERYING_PROPERTIES_MARKER] = None
            select.update(original)
        return select

    def build_filter(self, filter_expr, *args, **kwargs):
        # Check if the given filter expression is meant to use a queryable
        # property. Therefore, the possibility of filter_expr not being of the
        # correct type must be taken into account (a case Django would cover
        # already, but the check for queryable properties MUST run first).
        try:
            arg, value = filter_expr
        except (TypeError, ValueError):
            # Invalid value - just treat it as "no queryable property found"
            # and delegate it to Django.
            property_ref = None
        else:
            property_ref, lookups = resolve_queryable_property(self.model, QueryPath(arg))

        # If no queryable property could be determined for the filter
        # expression (either because a regular/non-existent field is referenced
        # or because the expression was a special or invalid value), call
        # Django's default implementation, which may in turn raise an
        # exception. Act the same way if the current top of the stack is used
        # to avoid infinite recursions.
        if not property_ref or (self._queryable_property_stack and self._queryable_property_stack[-1] == property_ref):
            return super().build_filter(filter_expr, *args, **kwargs)

        q_obj = property_ref.get_filter(lookups, value)
        # Before applying the filter implemented by the property, check if
        # the property signals the need of its own annotation to function.
        # If so, add the annotation first to avoid endless recursion, since
        # resolved filter will likely contain the same property name again.
        context = nullcontext()
        if property_ref.property.filter_requires_annotation:
            full_group_by = False
            context = self._add_queryable_property_annotation(property_ref, full_group_by)

        with context:
            # Luckily, build_filter and _add_q use the same return value
            # structure, so an _add_q call can be used to actually create the
            # return value for the current call.
            return self._add_q(q_obj, **convert_build_filter_to_add_q_kwargs(**kwargs))

    def get_aggregation(self, *args, **kwargs):
        # If the query is to be used as a pure aggregate query (which might use
        # a subquery), all queryable property annotations must be added to the
        # select mask to avoid potentially empty SELECT clauses.
        if self.annotation_select_mask is not None and self._queryable_property_annotations:
            annotation_names = (str(property_ref.full_path) for property_ref
                                in self._queryable_property_annotations)
            self.set_annotation_mask(set(self.annotation_select_mask).union(annotation_names))
        return super().get_aggregation(*args, **kwargs)

    def get_columns(self):  # Raw queries
        # Like QueryablePropertiesCompilerMixin.setup_query, but for raw
        # queries. The marker can simply be added as the first value as fields
        # are not strictly grouped like in regular queries.
        columns = super().get_columns()
        if self._use_querying_properties_marker:
            columns.insert(0, QUERYING_PROPERTIES_MARKER)
        return columns

    def get_compiler(self, *args, **kwargs):
        use_marker = self._use_querying_properties_marker
        self._use_querying_properties_marker = False
        compiler = super().get_compiler(*args, **kwargs)
        if use_marker:
            QueryablePropertiesCompilerMixin.inject_into_object(compiler)
        return compiler

    def names_to_path(self, names, *args, **kwargs):
        # This is a central method for resolving field names. To also allow the
        # use of queryable properties across relations, the relation path on
        # top of the stack must be prepended to trick Django into resolving
        # correctly.
        if self._queryable_property_stack:
            names = self._queryable_property_stack[-1].relation_path + names
        return super().names_to_path(names, *args, **kwargs)

    def resolve_ref(self, name, allow_joins=True, reuse=None, summarize=False, *args, **kwargs):
        # This method is used to resolve field names in complex expressions. If
        # a queryable property is used in such an expression, it needs to be
        # auto-annotated (while taking the stack into account) and returned.
        query_path = QueryPath(name)
        if self._queryable_property_stack:
            query_path = self._queryable_property_stack[-1].relation_path + query_path
        property_annotation = self._auto_annotate(query_path, full_group_by=False)
        if property_annotation:
            if summarize:
                # Outer queries for aggregations need refs to annotations of
                # the inner queries.
                from django.db.models.expressions import Ref
                return Ref(name, property_annotation)
            return property_annotation
        return super().resolve_ref(name, allow_joins, reuse, summarize, *args, **kwargs)

    def chain(self, *args, **kwargs):
        obj = super().chain(*args, **kwargs)
        return self._postprocess_clone(obj)
