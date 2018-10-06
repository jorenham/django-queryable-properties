# encoding: utf-8
import pytest

from django import VERSION as DJANGO_VERSION
from django.core.exceptions import FieldError
from django.db.models import F, Q
from django.utils import six

from queryable_properties.exceptions import QueryablePropertyError

from .models import (ApplicationWithClassBasedProperties, ApplicationWithDecoratorBasedProperties,
                     VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties)


@pytest.mark.django_db
class TestQueryFilters(object):

    @pytest.mark.parametrize('model, major_minor, expected_count', [
        (VersionWithClassBasedProperties, '1.2', 2),
        (VersionWithClassBasedProperties, '1.3', 4),
        (VersionWithClassBasedProperties, '2.0', 2),
        (VersionWithDecoratorBasedProperties, '1.2', 2),
        (VersionWithDecoratorBasedProperties, '1.3', 4),
        (VersionWithDecoratorBasedProperties, '2.0', 2),
    ])
    def test_simple_filter(self, versions, model, major_minor, expected_count):
        # Also test that using non-property filters still work and can be used
        # together with filters for queryable properties
        queryset = model.objects.filter(major_minor=major_minor, major=major_minor[0])
        assert len(queryset) == expected_count
        assert all(obj.major_minor == major_minor for obj in queryset)

    @pytest.mark.parametrize('model', [VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties])
    def test_filter_without_required_annotation(self, versions, model):
        # Filtering the 'version' property is also based on filtering the
        # 'major_minor' property, so this test also tests properties that build
        # on each other
        queryset = model.objects.filter(version='1.2.3')
        assert 'version' not in queryset.query.annotations
        assert all(obj.version == '1.2.3' for obj in queryset)

    @pytest.mark.skipif(DJANGO_VERSION < (1, 9), reason='using MIN/MAX in filters was not supported with sqlite')
    @pytest.mark.parametrize('model', [ApplicationWithClassBasedProperties, ApplicationWithDecoratorBasedProperties])
    def test_filter_with_required_annotation(self, versions, model):
        version_model = model.objects.first().versions.model
        version_model.objects.filter(version='2.0.0')[0].delete()
        queryset = model.objects.filter(highest_version='2.0.0')
        assert 'highest_version' in queryset.query.annotations
        assert len(queryset) == 1
        application = queryset[0]
        assert application.versions.filter(major=2, minor=0, patch=0).exists()
        # Check that a property annotation used implicitly by a filter does not
        # lead to a selection of the property annotation
        assert not model.highest_version._has_cached_value(application)

    @pytest.mark.parametrize('model', [VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties])
    def test_filter_implementation_used_despite_present_annotation(self, versions, model):
        queryset = model.objects.select_properties('version').filter(version='2.0.0')
        pseudo_sql = six.text_type(queryset.query)
        assert '"major" = 2' in pseudo_sql
        assert '"minor" = 0' in pseudo_sql
        assert '"patch" = 0' in pseudo_sql
        assert queryset.count() == 2

    @pytest.mark.parametrize('model', [VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties])
    def test_exception_on_unimplemented_filter(self, monkeypatch, model):
        monkeypatch.setattr(model.version, 'get_filter', None)
        with pytest.raises(QueryablePropertyError):
            model.objects.filter(version='1.2.3')

    @pytest.mark.parametrize('model', [VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties])
    def test_standard_exception_on_invalid_field_name(self, model):
        with pytest.raises(FieldError):
            model.objects.filter(non_existent_field=1337)

    @pytest.mark.skipif(DJANGO_VERSION < (1, 9), reason="type check didn't exist before Django 1.9")
    @pytest.mark.parametrize('model', [VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties])
    def test_standard_exception_on_invalid_filter_expression(self, model):
        with pytest.raises(FieldError):
            # The dict is passed as arg instead of kwargs, making it an invalid
            # filter expression.
            model.objects.filter(Q({'version': '2.0.0'}))


@pytest.mark.django_db
class TestNonModelInstanceQueries(object):

    @pytest.mark.parametrize('model, filters, expected_versions', [
        (VersionWithClassBasedProperties, {}, {'1.2.3', '1.3.0', '1.3.1', '2.0.0'}),
        (VersionWithClassBasedProperties, {'major_minor': '1.3'}, {'1.3.0', '1.3.1'}),
        (VersionWithClassBasedProperties, {'version': '2.0.0'}, {'2.0.0'}),
        (VersionWithDecoratorBasedProperties, {}, {'1.2.3', '1.3.0', '1.3.1', '2.0.0'}),
        (VersionWithDecoratorBasedProperties, {'major_minor': '1.3'}, {'1.3.0', '1.3.1'}),
        (VersionWithDecoratorBasedProperties, {'version': '2.0.0'}, {'2.0.0'}),
    ])
    def test_values(self, versions, model, filters, expected_versions):
        queryset = model.objects.filter(**filters).select_properties('version').values('version')
        assert (obj_dict['version'] in expected_versions for obj_dict in queryset)

    @pytest.mark.parametrize('model, filters, expected_versions', [
        (VersionWithClassBasedProperties, {}, {'1.2.3', '1.3.0', '1.3.1', '2.0.0'}),
        (VersionWithClassBasedProperties, {'major_minor': '1.3'}, {'1.3.0', '1.3.1'}),
        (VersionWithClassBasedProperties, {'version': '2.0.0'}, {'2.0.0'}),
        (VersionWithDecoratorBasedProperties, {}, {'1.2.3', '1.3.0', '1.3.1', '2.0.0'}),
        (VersionWithDecoratorBasedProperties, {'major_minor': '1.3'}, {'1.3.0', '1.3.1'}),
        (VersionWithDecoratorBasedProperties, {'version': '2.0.0'}, {'2.0.0'}),
    ])
    def test_values_list(self, versions, model, filters, expected_versions):
        queryset = model.objects.filter(**filters).select_properties('version').values_list('version', flat=True)
        assert (version in expected_versions for version in queryset)


@pytest.mark.django_db
class TestQueryAnnotations(object):

    @pytest.mark.parametrize('model, filters', [
        (VersionWithClassBasedProperties, {}),
        (VersionWithDecoratorBasedProperties, {}),
        (VersionWithClassBasedProperties, {'version': '1.2.3'}),
        (VersionWithDecoratorBasedProperties, {'version': '1.2.3'}),
    ])
    def test_cached_annotation_value(self, versions, model, filters):
        # Filter both before and after the select_properties call to check if
        # the annotation gets selected correctly regardless
        queryset = model.objects.filter(**filters).select_properties('version').filter(**filters)
        assert 'version' in queryset.query.annotations
        assert all(model.version._has_cached_value(obj) for obj in queryset)

    @pytest.mark.parametrize('model', [ApplicationWithClassBasedProperties, ApplicationWithDecoratorBasedProperties])
    def test_cached_annotation_value_with_group_by(self, versions, model):
        queryset = model.objects.select_properties('version_count')
        assert 'version_count' in queryset.query.annotations
        assert all(model.version_count._has_cached_value(obj) for obj in queryset)

    @pytest.mark.parametrize('model', [VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties])
    def test_annotation_based_on_queryable_property(self, versions, model):
        queryset = model.objects.annotate(annotation=F('version'))
        for version in queryset:
            assert version.version == version.annotation
            # Check that a property annotation used implicitly by another
            # annotation does not lead to a selection of the property
            # annotation
            assert not model.version._has_cached_value(version)

    @pytest.mark.parametrize('model', [VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties])
    def test_exception_on_unimplemented_annotater(self, model):
        with pytest.raises(QueryablePropertyError):
            model.objects.select_properties('major_minor')


@pytest.mark.django_db
class TestUpdateQueries(object):

    @pytest.mark.parametrize('model', [VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties])
    def test_simple_update(self, versions, model):
        queryset = model.objects.filter(major_minor='2.0')
        pks = list(queryset.values_list('pk', flat=True))
        assert queryset.update(major_minor='42.42') == len(pks)
        for pk in pks:
            version = model.objects.get(pk=pk)  # Reload from DB
            assert version.major_minor == '42.42'

    @pytest.mark.parametrize('model', [VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties])
    def test_update_based_on_other_property(self, versions, model):
        queryset = model.objects.filter(version='1.3.1')
        pks = list(queryset.values_list('pk', flat=True))
        assert queryset.update(version='1.3.37') == len(pks)
        for pk in pks:
            version = model.objects.get(pk=pk)  # Reload from DB
            assert version.version == '1.3.37'

    @pytest.mark.parametrize('model', [ApplicationWithClassBasedProperties, ApplicationWithDecoratorBasedProperties])
    def test_exception_on_unimplemented_updater(self, model):
        with pytest.raises(QueryablePropertyError):
            model.objects.update(highest_version='1.3.37')

    @pytest.mark.parametrize('model, kwargs', [
        (VersionWithClassBasedProperties, {'major_minor': '42.42', 'major': 18}),
        (VersionWithClassBasedProperties, {'major_minor': '42.42', 'version': '42.42.42'}),
        (VersionWithClassBasedProperties, {'major_minor': '1.2', 'version': '1.3.37', 'minor': 5}),
        (VersionWithDecoratorBasedProperties, {'major_minor': '42.42', 'major': 18}),
        (VersionWithDecoratorBasedProperties, {'major_minor': '42.42', 'version': '42.42.42'}),
        (VersionWithDecoratorBasedProperties, {'major_minor': '1.2', 'version': '1.3.37', 'minor': 5}),
    ])
    def test_exception_on_conflicting_values(self, model, kwargs):
        with pytest.raises(QueryablePropertyError):
            model.objects.update(**kwargs)
