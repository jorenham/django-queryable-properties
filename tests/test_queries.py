# encoding: utf-8
"""Tests for the compat, managers and query modules that perform actual queries."""

import pytest

from django import VERSION as DJANGO_VERSION
from django.core.exceptions import FieldError
from django.db import models
try:
    from django.db.models.functions import Concat
except ImportError:
    Concat = []  # This way, the name can be used in "and" expressions in parametrizations
from django.utils import six

from queryable_properties.exceptions import QueryablePropertyError

from .models import (ApplicationWithClassBasedProperties, ApplicationWithDecoratorBasedProperties,
                     CategoryWithClassBasedProperties, CategoryWithDecoratorBasedProperties,
                     VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties)


@pytest.mark.django_db
class TestQueryFilters(object):

    @pytest.mark.parametrize('model, filters, expected_count, expected_major_minor', [
        # Test that filter that don't involve queryable properties still work
        (VersionWithClassBasedProperties, models.Q(major=2, minor=0), 2, '2.0'),
        (VersionWithDecoratorBasedProperties, models.Q(major=2, minor=0), 2, '2.0'),
        (VersionWithClassBasedProperties, models.Q(minor=2) | models.Q(patch=3), 2, '1.2'),
        (VersionWithDecoratorBasedProperties, models.Q(minor=2) | models.Q(patch=3), 2, '1.2'),
        # All querysets are expected to return objects with the same
        # major_minor value (major_minor parameter).
        (VersionWithClassBasedProperties, models.Q(major_minor='1.2'), 2, '1.2'),
        (VersionWithDecoratorBasedProperties, models.Q(major_minor='1.2'), 2, '1.2'),
        # Also test that using non-property filters still work and can be used
        # together with filters for queryable properties
        (VersionWithClassBasedProperties, models.Q(major_minor='1.3') & models.Q(major=1), 4, '1.3'),
        (VersionWithDecoratorBasedProperties, models.Q(major_minor='1.3') & models.Q(major=1), 4, '1.3'),
        (VersionWithClassBasedProperties, models.Q(major_minor='1.3') | models.Q(patch=1), 4, '1.3'),
        (VersionWithDecoratorBasedProperties, models.Q(major_minor='1.3') | models.Q(patch=1), 4, '1.3'),
        # Also test nested filters
        (VersionWithClassBasedProperties, (models.Q(major_minor='2.0') | models.Q(patch=0)) & models.Q(minor=0),
         2, '2.0'),
        (VersionWithDecoratorBasedProperties, (models.Q(major_minor='2.0') | models.Q(patch=0)) & models.Q(minor=0),
         2, '2.0'),
    ])
    def test_simple_filter(self, versions, model, filters, expected_count, expected_major_minor):
        queryset = model.objects.filter(filters)
        assert len(queryset) == expected_count
        assert all(obj.major_minor == expected_major_minor for obj in queryset)

    @pytest.mark.parametrize('model', [VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties])
    def test_filter_without_required_annotation(self, versions, model):
        # Filtering the 'version' property is also based on filtering the
        # 'major_minor' property, so this test also tests properties that build
        # on each other
        queryset = model.objects.filter(version='1.2.3')
        assert 'version' not in queryset.query.annotations
        assert len(queryset) == 2
        assert all(obj.version == '1.2.3' for obj in queryset)

    @pytest.mark.parametrize('model', [ApplicationWithClassBasedProperties, ApplicationWithDecoratorBasedProperties])
    def test_filter_without_required_annotation_across_relation(self, versions, model):
        # Filtering the 'version' property is also based on filtering the
        # 'major_minor' property, so this test also tests properties that build
        # on each other
        queryset = model.objects.filter(versions__version='1.2.3')
        assert 'versions__version' not in queryset.query.annotations
        assert len(queryset) == 2
        assert all(obj.versions.filter(version='1.2.3').exists() for obj in queryset)

    @pytest.mark.parametrize('model, filters, expected_count', [
        (ApplicationWithClassBasedProperties, models.Q(version_count__gt=3), 2),
        (ApplicationWithClassBasedProperties, models.Q(version_count=4, name__contains='cool'), 1),
        (ApplicationWithClassBasedProperties, models.Q(version_count=4) | models.Q(name__contains='cool'), 2),
        (ApplicationWithClassBasedProperties, models.Q(version_count__gt=3, major_sum__gt=5), 0),
        (ApplicationWithClassBasedProperties, models.Q(version_count__gt=3) | models.Q(major_sum__gt=5), 2),
        (ApplicationWithDecoratorBasedProperties, models.Q(version_count__gt=3), 2),
        (ApplicationWithDecoratorBasedProperties, models.Q(version_count=4, name__contains='cool'), 1),
        (ApplicationWithDecoratorBasedProperties, models.Q(version_count=4) | models.Q(name__contains='cool'), 2),
        (ApplicationWithDecoratorBasedProperties, models.Q(version_count__gt=3, major_sum__gt=5), 0),
        (ApplicationWithDecoratorBasedProperties, models.Q(version_count__gt=3) | models.Q(major_sum__gt=5), 2),
    ])
    def test_filter_with_required_aggregate_annotation(self, versions, model, filters, expected_count):
        queryset = model.objects.filter(filters)
        assert 'version_count' in queryset.query.annotations
        assert len(queryset) == expected_count
        # Check that a property annotation used implicitly by a filter does not
        # lead to a selection of the property annotation
        assert all(not model.version_count._has_cached_value(app) for app in queryset)

    @pytest.mark.parametrize('model', [CategoryWithClassBasedProperties, CategoryWithDecoratorBasedProperties])
    def test_filter_with_required_aggregate_annotation_across_relation(self, versions, model):
        # A query containing an aggregate across a relation is still only
        # grouped by fields of the query's model, so in this case the version
        # count is the total number of application versions per category.
        queryset = model.objects.filter(applications__version_count=4)
        assert 'applications__version_count' in queryset.query.annotations
        assert queryset.count() == 1
        assert model.objects.filter(applications__version_count=8).count() == 1

    @pytest.mark.skipif(DJANGO_VERSION < (1, 8), reason="Expression-based annotations didn't exist before Django 1.8")
    @pytest.mark.parametrize('model', [VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties])
    def test_filter_with_required_expression_annotation(self, versions, model):
        queryset = model.objects.filter(changes_or_default='(No data)')
        assert 'changes_or_default' in queryset.query.annotations
        assert len(queryset) == 6
        assert all(obj.changes_or_default == '(No data)' for obj in queryset)
        # Check that a property annotation used implicitly by a filter does not
        # lead to a selection of the property annotation
        assert all(not model.changes_or_default._has_cached_value(version) for version in queryset)

    @pytest.mark.skipif(DJANGO_VERSION < (1, 8), reason="Expression-based annotations didn't exist before Django 1.8")
    @pytest.mark.parametrize('model', [ApplicationWithClassBasedProperties, ApplicationWithDecoratorBasedProperties])
    def test_filter_with_required_expression_annotation_across_relation(self, versions, model):
        queryset = model.objects.filter(versions__changes_or_default='(No data)')
        assert 'versions__changes_or_default' in queryset.query.annotations
        assert len(queryset) == 6
        assert all(obj.versions.filter(changes_or_default='(No data)').exists() for obj in queryset)
        assert queryset.distinct().count() == 2

    @pytest.mark.skipif(DJANGO_VERSION < (1, 8), reason="Expression-based annotations didn't exist before Django 1.8")
    @pytest.mark.parametrize('model', [CategoryWithClassBasedProperties, CategoryWithDecoratorBasedProperties])
    def test_filter_with_required_expression_annotation_and_dependency_across_relation(self, versions, model):
        queryset = model.objects.filter(applications__lowered_version_changes='amazing new features')
        assert 'applications__lowered_version_changes' in queryset.query.annotations
        assert len(queryset) == 3
        assert queryset.distinct().count() == 2

    @pytest.mark.skipif(DJANGO_VERSION < (1, 11), reason="Explicit subqueries didn't exist before Django 1.11")
    @pytest.mark.parametrize('model', [ApplicationWithClassBasedProperties, ApplicationWithDecoratorBasedProperties])
    def test_filter_with_required_subquery_annotation(self, versions, model):
        version_model = model.objects.all()[0].versions.model
        version_model.objects.filter(version='2.0.0')[0].delete()
        queryset = model.objects.filter(highest_version='2.0.0')
        assert 'highest_version' in queryset.query.annotations
        assert len(queryset) == 1
        application = queryset[0]
        assert application.versions.filter(major=2, minor=0, patch=0).exists()
        # Check that a property annotation used implicitly by a filter does not
        # lead to a selection of the property annotation
        assert not model.highest_version._has_cached_value(application)

    @pytest.mark.skipif(DJANGO_VERSION < (1, 11), reason="Explicit subqueries didn't exist before Django 1.11")
    @pytest.mark.parametrize('model', [VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties])
    def test_filter_with_required_subquery_annotation_across_relation(self, versions, model):
        model.objects.filter(version='2.0.0')[0].delete()
        queryset = model.objects.filter(application__highest_version='2.0.0')
        assert 'application__highest_version' in queryset.query.annotations
        assert len(queryset) == 4
        assert all(obj.application.highest_version == '2.0.0' for obj in queryset)

    @pytest.mark.parametrize('model', [ApplicationWithClassBasedProperties, ApplicationWithDecoratorBasedProperties])
    def test_filter_implementation_used_despite_present_annotation(self, monkeypatch, versions, model):
        # Patch the property to have a filter that is always True, then use a
        # condition that would be False without the patch.
        monkeypatch.setattr(model.version_count, 'get_filter', lambda cls, lookup, value: models.Q(pk__gt=0))
        queryset = model.objects.select_properties('version_count').filter(version_count__gt=5)
        assert '"id" > 0' in six.text_type(queryset.query)
        assert queryset.count() == 2

    @pytest.mark.skipif(DJANGO_VERSION < (1, 8), reason="Expression-based annotations didn't exist before Django 1.8")
    @pytest.mark.parametrize('model', [VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties])
    def test_filter_implementation_used_despite_present_expression_annotation(self, versions, model):
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
            model.objects.filter(models.Q({'version': '2.0.0'}))


@pytest.mark.django_db
class TestNonModelInstanceQueries(object):

    @pytest.mark.parametrize('model, filters, expected_version_counts', [
        (ApplicationWithClassBasedProperties, {}, {3, 4}),
        (ApplicationWithClassBasedProperties, {'version_count__gt': 3}, {4}),
        (ApplicationWithClassBasedProperties, {'version_count': 5}, {}),
        (ApplicationWithDecoratorBasedProperties, {}, {3, 4}),
        (ApplicationWithDecoratorBasedProperties, {'version_count__gt': 3}, {4}),
        (ApplicationWithDecoratorBasedProperties, {'version_count': 5}, {}),
    ])
    def test_aggregate_values_after_annotate(self, versions, model, filters, expected_version_counts):
        # Delete one version to create separate version counts
        model.objects.all()[0].versions.all()[0].delete()
        queryset = model.objects.filter(**filters).select_properties('version_count').values('version_count')
        assert all(obj_dict['version_count'] in expected_version_counts for obj_dict in queryset)

    @pytest.mark.skipif(DJANGO_VERSION < (1, 8), reason="Expression-based annotations didn't exist before Django 1.8")
    @pytest.mark.parametrize('model, filters, expected_versions', [
        (VersionWithClassBasedProperties, {}, {'1.2.3', '1.3.0', '1.3.1', '2.0.0'}),
        (VersionWithClassBasedProperties, {'major_minor': '1.3'}, {'1.3.0', '1.3.1'}),
        (VersionWithClassBasedProperties, {'version': '2.0.0'}, {'2.0.0'}),
        (VersionWithDecoratorBasedProperties, {}, {'1.2.3', '1.3.0', '1.3.1', '2.0.0'}),
        (VersionWithDecoratorBasedProperties, {'major_minor': '1.3'}, {'1.3.0', '1.3.1'}),
        (VersionWithDecoratorBasedProperties, {'version': '2.0.0'}, {'2.0.0'}),
    ])
    def test_expression_values_after_annotate(self, versions, model, filters, expected_versions):
        queryset = model.objects.filter(**filters).select_properties('version').values('version')
        assert all(obj_dict['version'] in expected_versions for obj_dict in queryset)

    @pytest.mark.parametrize('model', [ApplicationWithClassBasedProperties, ApplicationWithDecoratorBasedProperties])
    def test_values_before_annotate(self, versions, model):
        values = model.objects.values('common_data').select_properties('version_count')
        assert len(values) == 1
        assert values[0]['version_count'] == len(versions) / 2

    @pytest.mark.parametrize('model, filters, expected_version_counts', [
        (ApplicationWithClassBasedProperties, {}, {3, 4}),
        (ApplicationWithClassBasedProperties, {'version_count__gt': 3}, {4}),
        (ApplicationWithClassBasedProperties, {'version_count': 5}, {}),
        (ApplicationWithDecoratorBasedProperties, {}, {3, 4}),
        (ApplicationWithDecoratorBasedProperties, {'version_count__gt': 3}, {4}),
        (ApplicationWithDecoratorBasedProperties, {'version_count': 5}, {}),
    ])
    def test_aggregate_values_list(self, versions, model, filters, expected_version_counts):
        queryset = model.objects.filter(**filters).select_properties('version_count').values_list('version_count',
                                                                                                  flat=True)
        assert all(version_count in expected_version_counts for version_count in queryset)

    @pytest.mark.skipif(DJANGO_VERSION < (1, 8), reason="Expression-based annotations didn't exist before Django 1.8")
    @pytest.mark.parametrize('model, filters, expected_versions', [
        (VersionWithClassBasedProperties, {}, {'1.2.3', '1.3.0', '1.3.1', '2.0.0'}),
        (VersionWithClassBasedProperties, {'major_minor': '1.3'}, {'1.3.0', '1.3.1'}),
        (VersionWithClassBasedProperties, {'version': '2.0.0'}, {'2.0.0'}),
        (VersionWithDecoratorBasedProperties, {}, {'1.2.3', '1.3.0', '1.3.1', '2.0.0'}),
        (VersionWithDecoratorBasedProperties, {'major_minor': '1.3'}, {'1.3.0', '1.3.1'}),
        (VersionWithDecoratorBasedProperties, {'version': '2.0.0'}, {'2.0.0'}),
    ])
    def test_expression_values_list(self, versions, model, filters, expected_versions):
        queryset = model.objects.filter(**filters).select_properties('version').values_list('version', flat=True)
        assert all(version in expected_versions for version in queryset)


@pytest.mark.django_db
class TestQueryAnnotations(object):

    @pytest.mark.parametrize('model, filters', [
        (ApplicationWithClassBasedProperties, {}),
        (ApplicationWithClassBasedProperties, {'version_count__gt': 3}),
        (ApplicationWithDecoratorBasedProperties, {}),
        (ApplicationWithDecoratorBasedProperties, {'version_count__gt': 3}),
    ])
    def test_cached_aggregate_annotation_value(self, versions, model, filters):
        # Filter both before and after the select_properties call to check if
        # the annotation gets selected correctly regardless
        queryset = model.objects.filter(**filters).select_properties('version_count', 'major_sum').filter(**filters)
        assert 'version_count' in queryset.query.annotations
        assert 'major_sum' in queryset.query.annotations
        assert all(model.version_count._has_cached_value(obj) for obj in queryset)
        assert all(model.major_sum._has_cached_value(obj) for obj in queryset)

    @pytest.mark.skipif(DJANGO_VERSION < (1, 8), reason="Expression-based annotations didn't exist before Django 1.8")
    @pytest.mark.parametrize('model, filters', [
        (VersionWithClassBasedProperties, {}),
        (VersionWithDecoratorBasedProperties, {}),
        (VersionWithClassBasedProperties, {'version': '1.2.3'}),
        (VersionWithDecoratorBasedProperties, {'version': '1.2.3'}),
    ])
    def test_cached_expression_annotation_value(self, versions, model, filters):
        # Filter both before and after the select_properties call to check if
        # the annotation gets selected correctly regardless
        queryset = model.objects.filter(**filters).select_properties('version').filter(**filters)
        assert 'version' in queryset.query.annotations
        assert all(model.version._has_cached_value(obj) for obj in queryset)

    @pytest.mark.skipif(DJANGO_VERSION < (1, 8), reason="Expression-based annotations didn't exist before Django 1.8")
    @pytest.mark.parametrize('model, annotation, expected_value', [
        (VersionWithClassBasedProperties, models.F('version'), '{}'),
        (VersionWithDecoratorBasedProperties, models.F('version'), '{}'),
    ] + (Concat and [  # The next test parametrizations are only active if Concat is defined
        (VersionWithClassBasedProperties, Concat(models.Value('V'), 'version'), 'V{}'),
        (VersionWithDecoratorBasedProperties, Concat(models.Value('V'), 'version'), 'V{}'),
    ]))
    def test_annotation_based_on_queryable_property(self, versions, model, annotation, expected_value):
        queryset = model.objects.annotate(annotation=annotation)
        for version in queryset:
            assert version.annotation == expected_value.format(version.version)
            # Check that a property annotation used implicitly by another
            # annotation does not lead to a selection of the property
            # annotation
            assert not model.version._has_cached_value(version)

    @pytest.mark.parametrize('model, limit, expected_total', [
        (ApplicationWithClassBasedProperties, None, 8),
        (ApplicationWithClassBasedProperties, 1, 4),
        (ApplicationWithDecoratorBasedProperties, None, 8),
        (ApplicationWithDecoratorBasedProperties, 1, 4),
    ])
    def test_aggregate_based_on_queryable_property(self, versions, model, limit, expected_total):
        result = model.objects.all()[:limit].aggregate(total_version_count=models.Sum('version_count'))
        assert result['total_version_count'] == expected_total

    @pytest.mark.parametrize('model, limit, expected_total', [
        (VersionWithClassBasedProperties, None, 32),
        (VersionWithClassBasedProperties, 4, 16),
        (VersionWithDecoratorBasedProperties, None, 32),
        (VersionWithDecoratorBasedProperties, 4, 16),
    ])
    def test_aggregate_based_on_queryable_property_across_relation(self, versions, model, limit, expected_total):
        result = model.objects.all()[:limit].aggregate(total_version_count=models.Sum('application__version_count'))
        assert result['total_version_count'] == expected_total

    @pytest.mark.parametrize('model', [ApplicationWithClassBasedProperties, ApplicationWithDecoratorBasedProperties])
    def test_iterator_with_aggregate_annotation(self, versions, model):
        queryset = model.objects.filter(version_count=4).select_properties('version_count')
        for application in queryset.iterator():
            assert model.version_count._has_cached_value(application)
            assert application.version_count == 4
        assert queryset._result_cache is None

    @pytest.mark.skipif(DJANGO_VERSION < (1, 8), reason="Expression-based annotations didn't exist before Django 1.8")
    @pytest.mark.parametrize('model', [VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties])
    def test_iterator_with_expression_annotation(self, versions, model):
        queryset = model.objects.filter(major_minor='2.0').select_properties('version')
        for version in queryset.iterator():
            assert model.version._has_cached_value(version)
            assert version.version == '2.0.0'
        assert queryset._result_cache is None

    @pytest.mark.parametrize('model', [ApplicationWithClassBasedProperties, ApplicationWithDecoratorBasedProperties])
    def test_removed_annotation(self, versions, model):
        """
        Test that queries can still be performed even if queryable property annotations have been manually removed from
        the queryset.
        """
        queryset = model.objects.select_properties('version_count')
        del queryset.query.annotations['version_count']
        assert bool(queryset)
        assert all(not model.version_count._has_cached_value(obj) for obj in queryset)

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

    @pytest.mark.parametrize('model, update_kwargs', [
        (VersionWithClassBasedProperties, {'version': '1.3.37'}),
        (VersionWithDecoratorBasedProperties, {'version': '1.3.37'}),
        # Also test that setting the same field(s) via multiple queryable
        # properties works as long as they try to set the same values
        (VersionWithClassBasedProperties, {'version': '1.3.37', 'major_minor': '1.3'}),
        (VersionWithDecoratorBasedProperties, {'version': '1.3.37', 'major_minor': '1.3'}),
    ])
    def test_update_based_on_other_property(self, versions, model, update_kwargs):
        queryset = model.objects.filter(version='1.3.1')
        pks = list(queryset.values_list('pk', flat=True))
        assert queryset.update(**update_kwargs) == len(pks)
        for pk in pks:
            version = model.objects.get(pk=pk)  # Reload from DB
            assert version.version == update_kwargs['version']

    @pytest.mark.parametrize('model', [ApplicationWithClassBasedProperties, ApplicationWithDecoratorBasedProperties])
    def test_exception_on_unimplemented_updater(self, model):
        with pytest.raises(QueryablePropertyError):
            model.objects.update(highest_version='1.3.37')

    @pytest.mark.parametrize('model, kwargs', [
        (VersionWithClassBasedProperties, {'major_minor': '42.42', 'major': 18}),
        (VersionWithClassBasedProperties, {'major_minor': '1.2', 'version': '1.3.37', 'minor': 5}),
        (VersionWithDecoratorBasedProperties, {'major_minor': '42.42', 'major': 18}),
        (VersionWithDecoratorBasedProperties, {'major_minor': '1.2', 'version': '1.3.37', 'minor': 5}),
    ])
    def test_exception_on_conflicting_values(self, model, kwargs):
        with pytest.raises(QueryablePropertyError):
            model.objects.update(**kwargs)


@pytest.mark.django_db
class TestQueryOrdering(object):

    @pytest.mark.parametrize('model, order_by, reverse, with_selection', [
        (ApplicationWithClassBasedProperties, 'version_count', False, False),
        (ApplicationWithDecoratorBasedProperties, 'version_count', False, False),
        (ApplicationWithClassBasedProperties, 'version_count', False, True),
        (ApplicationWithDecoratorBasedProperties, 'version_count', False, True),
        (ApplicationWithClassBasedProperties, '-version_count', True, False),
        (ApplicationWithDecoratorBasedProperties, '-version_count', True, False),
        (ApplicationWithClassBasedProperties, '-version_count', True, True),
        (ApplicationWithDecoratorBasedProperties, '-version_count', True, True),
    ])
    def test_order_by_property_with_aggregate_annotation(self, versions, model, order_by, reverse, with_selection):
        model.objects.all()[0].versions.all()[0].delete()
        queryset = model.objects.all()
        if with_selection:
            queryset = queryset.select_properties('version_count')
        results = list(queryset.order_by(order_by))
        assert results == sorted(results, key=lambda application: application.version_count, reverse=reverse)
        # Check that ordering by a property annotation does not lead to a
        # selection of the property annotation
        assert all(model.version_count._has_cached_value(application) is with_selection for application in results)

    @pytest.mark.skipif(DJANGO_VERSION < (1, 8), reason="Expression-based annotations didn't exist before Django 1.8")
    @pytest.mark.parametrize('model, order_by, reverse, with_selection', [
        # All parametrizations are expected to yield results ordered by the
        # full version (ASC/DESC depending on the reverse parameter).
        (VersionWithClassBasedProperties, 'version', False, False),
        (VersionWithDecoratorBasedProperties, 'version', False, False),
        (VersionWithClassBasedProperties, 'version', False, True),
        (VersionWithDecoratorBasedProperties, 'version', False, True),
        (VersionWithClassBasedProperties, '-version', True, False),
        (VersionWithDecoratorBasedProperties, '-version', True, False),
        (VersionWithClassBasedProperties, '-version', True, True),
        (VersionWithDecoratorBasedProperties, '-version', True, True),
    ] + (Concat and [  # The next test parametrizations are only active if Concat is defined
        (VersionWithClassBasedProperties, Concat(models.Value('V'), 'version').asc(), False, False),
        (VersionWithDecoratorBasedProperties, Concat(models.Value('V'), 'version').asc(), False, False),
        (VersionWithClassBasedProperties, Concat(models.Value('V'), 'version').asc(), False, True),
        (VersionWithDecoratorBasedProperties, Concat(models.Value('V'), 'version').asc(), False, True),
        (VersionWithClassBasedProperties, Concat(models.Value('V'), 'version').desc(), True, False),
        (VersionWithDecoratorBasedProperties, Concat(models.Value('V'), 'version').desc(), True, False),
        (VersionWithClassBasedProperties, Concat(models.Value('V'), 'version').desc(), True, True),
        (VersionWithDecoratorBasedProperties, Concat(models.Value('V'), 'version').desc(), True, True),
    ]))
    def test_order_by_property_with_annotater(self, versions, model, order_by, reverse, with_selection):
        queryset = model.objects.all()
        if with_selection:
            queryset = queryset.select_properties('version')
        results = list(queryset.order_by(order_by))
        assert results == sorted(results, key=lambda version: version.version, reverse=reverse)
        # Check that ordering by a property annotation does not lead to a
        # selection of the property annotation
        assert all(model.version._has_cached_value(version) is with_selection for version in results)

    @pytest.mark.parametrize('model', [VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties])
    def test_order_by_property_with_annotater_across_relation(self, versions, model):
        model.objects.all()[0].delete()  # Create a different version count for the application fixtures
        results = list(model.objects.order_by('application__version_count'))
        assert results == sorted(results, key=lambda version: version.application.version_count)

    @pytest.mark.parametrize('model', [VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties])
    def test_exception_on_unimplemented_annotater(self, model):
        with pytest.raises(QueryablePropertyError):
            iter(model.objects.order_by('major_minor'))
