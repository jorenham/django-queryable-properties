# -*- coding: utf-8 -*-

from django.contrib.admin import ModelAdmin, StackedInline, TabularInline

from ..compat import ADMIN_QUERYSET_METHOD_NAME, admin_validation, chain_queryset
from ..exceptions import QueryablePropertyError
from ..managers import QueryablePropertiesQuerySetMixin
from .checks import QueryablePropertiesChecksMixin
from .filters import QueryablePropertyField


class QueryablePropertiesAdminMixin(object):

    list_select_properties = ()

    @classmethod
    def validate(cls, model):  # pragma: no cover
        cls._ensure_property_checks()
        return super(QueryablePropertiesAdminMixin, cls).validate(model)

    def check(self, model=None, **kwargs):
        if model:  # pragma: no cover
            kwargs['model'] = model
        self._ensure_property_checks(self)
        return super(QueryablePropertiesAdminMixin, self).check(**kwargs)

    if getattr(getattr(ModelAdmin, 'check', None), '__self__', None):  # pragma: no cover
        # In old Django versions, check was a classmethod.
        check = classmethod(check)

    @classmethod
    def _ensure_property_checks(cls, obj=None):
        obj = obj or cls
        # Dynamically add a mixin that handles queryable properties into the
        # admin's checks/validation class.
        for attr_name in ('checks_class', 'validator_class', 'default_validator_class'):
            checks_class = getattr(obj, attr_name, None)
            if checks_class and not issubclass(checks_class, QueryablePropertiesChecksMixin):
                class_name = 'QueryableProperties' + checks_class.__name__
                setattr(obj, attr_name, QueryablePropertiesChecksMixin.mix_with_class(checks_class, class_name))

    def get_queryset(self, request):
        # The base method has different names in different Django versions (see
        # comment on the constant definition).
        base_method = getattr(super(QueryablePropertiesAdminMixin, self), ADMIN_QUERYSET_METHOD_NAME)
        queryset = base_method(request)
        # Make sure to use a queryset with queryable properties features.
        if not isinstance(queryset, QueryablePropertiesQuerySetMixin):
            queryset = chain_queryset(queryset)
            QueryablePropertiesQuerySetMixin.inject_into_object(queryset)
        # Apply list_select_properties.
        list_select_properties = self.get_list_select_properties(request)
        if list_select_properties:
            queryset = queryset.select_properties(*list_select_properties)
        return queryset

    def queryset(self, request):  # pragma: no cover
        # Same as get_queryset, but for very old Django versions. Simply
        # delegate to need_having, which is aware of the different methods in
        # different versions and therefore calls the correct super methods if
        # necessary.
        return self.get_queryset(request)

    def get_list_filter(self, request):
        list_filter = super(QueryablePropertiesAdminMixin, self).get_list_filter(request)
        expanded_filters = []
        for item in list_filter:
            if not callable(item):
                if isinstance(item, (tuple, list)):
                    field_name, filter_class = item
                else:
                    field_name, filter_class = item, None
                try:
                    item = QueryablePropertyField(self, field_name).get_filter_creator(filter_class)
                except QueryablePropertyError:
                    pass
            expanded_filters.append(item)
        return expanded_filters

    def get_list_select_properties(self, request):
        return self.list_select_properties


class QueryablePropertiesAdmin(QueryablePropertiesAdminMixin, ModelAdmin):

    pass


class QueryablePropertiesStackedInline(QueryablePropertiesAdminMixin, StackedInline):

    pass


class QueryablePropertiesTabularInline(QueryablePropertiesAdminMixin, TabularInline):

    pass


# In very old django versions, the admin validation happens in one big function
# that cannot really be extended well. Therefore, the Django module will be
# monkeypatched in order to allow the queryable properties validation to take
# effect.
django_validate = getattr(admin_validation, 'validate', None)
django_validate_inline = getattr(admin_validation, 'validate_inline', None)

if django_validate:  # pragma: no cover
    def validate(cls, model):
        if issubclass(cls, QueryablePropertiesAdminMixin):
            cls = QueryablePropertiesChecksMixin()._validate_queryable_properties(cls, model)
        django_validate(cls, model)

    admin_validation.validate = validate

if django_validate_inline:  # pragma: no cover
    def validate_inline(cls, parent, parent_model):
        if issubclass(cls, QueryablePropertiesAdminMixin):
            cls = QueryablePropertiesChecksMixin()._validate_queryable_properties(cls, cls.model)
        django_validate_inline(cls, parent, parent_model)

    admin_validation.validate_inline = validate_inline
