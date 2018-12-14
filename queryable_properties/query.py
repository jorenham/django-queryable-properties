# encoding: utf-8

from contextlib import contextmanager

from .compat import (ADD_Q_METHOD_NAME, ANNOTATION_TO_AGGREGATE_ATTRIBUTES_MAP, BUILD_FILTER_METHOD_NAME,
                     convert_build_filter_to_add_q_kwargs, LOOKUP_SEP)
from .exceptions import QueryablePropertyDoesNotExist, QueryablePropertyError
from .utils import get_queryable_property


class QueryablePropertiesQueryMixin(object):
    """
    A mixin for :class:`django.db.models.sql.Query` objects that extends the
    original Django objects to deal with queryable properties, e.g. managing
    used properties or automatically add required properties as annotations.
    """

    def __init__(self, *args, **kwargs):
        super(QueryablePropertiesQueryMixin, self).__init__(*args, **kwargs)
        # Stores queryable properties used as annotations in this query along
        # with the information if the annotated value should be selected.
        self._queryable_property_annotations = {}
        # A stack for queryable properties whose annotations are currently
        # required while filtering.
        self._required_annotation_stack = []

    def __getattr__(self, name):  # pragma: no cover
        # Redirect some attribute accesses for older Django versions (where
        # annotations were tied to aggregations, hence "aggregation" in the
        # names instead of "annotation").
        if name in ANNOTATION_TO_AGGREGATE_ATTRIBUTES_MAP:
            return getattr(self, ANNOTATION_TO_AGGREGATE_ATTRIBUTES_MAP[name])
        raise AttributeError()

    @contextmanager
    def _required_annotation(self, prop=None):
        """
        Context manager to add the given queryable property to the top of the
        required annotation stack when entering and removing it again when
        exiting. Intended to be used when queryable properties with
        filter_requires_annotation=True are used to filter. The given property
        may be None (which won't change the stack at all) to simplify any code
        using the context manager.

        :param queryable_properties.properties.QueryableProperty prop:
            The property that should act as the current context.
        """
        if prop:
            self._required_annotation_stack.append(prop)
        yield self
        if prop:
            self._required_annotation_stack.pop()

    def _resolve_queryable_property(self, path):
        """
        Resolve the given path into a queryable property on the model
        associated with this query.

        :param collections.Sequence path: The path to resolve (a string of
                                          Django's query expression split up
                                          by the lookup separator).
        :return: The queryable property (if one could be resolved) or None.
        :rtype: queryable_properties.properties.QueryableProperty | None
        """
        # Currently, only properties defined directly at the model associated
        # with this query are supported. Therefore only check the first part
        # of the path.
        try:
            return get_queryable_property(self.model, path[0])
        except QueryablePropertyDoesNotExist:
            return None

    def _auto_annotate(self, path):
        """
        Try to resolve the given path into a queryable property and annotate
        the property as a non-selected property (if the property wasn't added
        as an annotation already). Do nothing if the path does not match a
        queryable property.

        :param collections.Sequence path: The path to resolve (a string of
                                          Django's query expression split up
                                          by the lookup separator).
        :return: The resolved annotation or None if the path couldn't be
                 resolved.
        """
        prop = self._resolve_queryable_property(path)
        return prop and self.add_queryable_property_annotation(prop)

    def add_queryable_property_annotation(self, prop, select=False):
        """
        Add an annotation for the given queryable property to this query (if
        it wasn't annotated already). An exception will be raised if the
        property to add does not support annotation creation.

        :param queryable_properties.properties.QueryableProperty prop:
            The property to add an annotation for.
        :param bool select: Signals whether the annotation should be selected
                            or not.
        :return: The resolved annotation.
        """
        if prop not in self._queryable_property_annotations:
            if not prop.get_annotation:
                raise QueryablePropertyError('Queryable property "{}" needs to be added as annotation but does not '
                                             'implement annotation creation.'.format(prop.name))
            self.add_annotation(prop.get_annotation(self.model), alias=prop.name, is_summary=False)
            # Perform the required GROUP BY setup if the annotation contained
            # aggregates, which is normally done by QuerySet.annotate. In older
            # Django versions, the contains_aggregate attribute didn't exist,
            # but aggregates are always assumed in this case since annotations
            # were strongly tied to aggregates.
            if getattr(self.annotations[prop.name], 'contains_aggregate', True) and self.group_by is not True:
                self.set_group_by()
        self._queryable_property_annotations[prop] = self._queryable_property_annotations.get(prop, False) or select
        return self.annotations[prop.name]

    def add_aggregate(self, aggregate, model=None, alias=None, is_summary=False):  # pragma: no cover
        # This method is called in older versions to add an aggregation or
        # annotation. Since both might be based on a queryable property, an
        # auto-annotation has to occur here.
        self._auto_annotate(aggregate.lookup.split(LOOKUP_SEP))
        # The overridden method also allows to set a default value for the
        # model parameter, which will be missing if add_annotation calls are
        # redirected to add_aggregate for older Django versions.
        model = model or self.model
        return super(QueryablePropertiesQueryMixin, self).add_aggregate(aggregate, model, alias, is_summary)

    def add_filter(self, *args, **kwargs):  # pragma: no cover
        # The build_filter method was called add_filter in very old Django
        # versions. Since recent versions still have an add_filter method (for
        # different purposes), the queryable properties customizations should
        # only occur in old versions.
        if BUILD_FILTER_METHOD_NAME == 'add_filter':
            # Simply use the build_filter implementation that does all the
            # having lifting and is aware of the different methods in different
            # versions and therefore calls the correct super methods if
            # necessary.
            return self.build_filter(*args, **kwargs)
        return super(QueryablePropertiesQueryMixin, self).add_filter(*args, **kwargs)

    def build_filter(self, filter_expr, *args, **kwargs):
        # Check if the given filter expression is meant to use a queryable
        # property. Therefore, the possibility of filter_expr not being of the
        # correct type must be taken into account (a case Django would cover
        # already, but the check for queryable properties MUST run first).
        try:
            arg, value = filter_expr
        except ValueError:
            # Invalid value - just treat it as "no queryable property found",
            # delegate it to Django and let it generate the exception.
            path = prop = None
        else:
            path = arg.split(LOOKUP_SEP)
            prop = self._resolve_queryable_property(path)

        # If no queryable property could be determined for the filter
        # expression (either because a regular/non-existent field is referenced
        # or because the expression was an invalid value), call Django's
        # default implementation, which may in turn raise an exception. Act the
        # same way if the current top of the required annotation stack is used
        # to avoid endless recursions.
        if not prop or (self._required_annotation_stack and self._required_annotation_stack[-1] == prop):
            # The base method has different names in different Django versions
            # (see comment on the constant definition).
            base_method = getattr(super(QueryablePropertiesQueryMixin, self), BUILD_FILTER_METHOD_NAME)
            return base_method(filter_expr, *args, **kwargs)

        if not prop.get_filter:
            raise QueryablePropertyError('Queryable property "{}" is supposed to be used as a filter but does not '
                                         'implement filtering.'.format(prop.name))

        # Before applying the filter implemented by the property, check if
        # the property signals the need of its own annotation to function.
        # If so, add the annotation first to avoid endless recursion, since
        # resolved filter will likely contain the same property name again.
        required_annotation_prop = None
        if prop.filter_requires_annotation:
            self.add_queryable_property_annotation(prop)
            required_annotation_prop = prop
        lookup = path[1] if len(path) > 1 else 'exact'
        q_object = prop.get_filter(self.model, lookup, value)
        # Luckily, build_filter and _add_q use the same return value
        # structure, so an _add_q call can be used to actually create the
        # return value for the current call.
        with self._required_annotation(required_annotation_prop):
            # The (_)add_q method has different names in different Django
            # versions (see comment on the constant definition).
            method = getattr(self, ADD_Q_METHOD_NAME)
            return method(q_object, **convert_build_filter_to_add_q_kwargs(**kwargs))

    def resolve_ref(self, name, allow_joins=True, reuse=None, summarize=False):
        # This method is used to resolve field names in complex expressions. If
        # a queryable property is used in such an expression, it needs to be
        # auto-annotated and returned here.
        property_annotation = self._auto_annotate([name])
        if property_annotation:
            if summarize:
                # Outer queries for aggregations need refs to annotations of
                # the inner queries
                from django.db.models.expressions import Ref
                return Ref(name, property_annotation)
            else:
                return property_annotation
        return super(QueryablePropertiesQueryMixin, self).resolve_ref(name, allow_joins, reuse, summarize)

    def clone(self, *args, **kwargs):
        obj = super(QueryablePropertiesQueryMixin, self).clone(*args, **kwargs)
        obj._queryable_property_annotations = dict(self._queryable_property_annotations)
        obj._required_annotation_stack = list(self._required_annotation_stack)
        return obj