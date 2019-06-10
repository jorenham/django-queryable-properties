# encoding: utf-8

import pytest

from django import VERSION as DJANGO_VERSION
from django.db import models

from ..conftest import Concat, Value
from ..models import (ApplicationWithClassBasedProperties, ApplicationWithDecoratorBasedProperties,
                      VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('versions')]


class TestAggregateAnnotations(object):

    @pytest.mark.parametrize('model, filters', [
        (ApplicationWithClassBasedProperties, {}),
        (ApplicationWithClassBasedProperties, {'version_count__gt': 3}),
        (ApplicationWithDecoratorBasedProperties, {}),
        (ApplicationWithDecoratorBasedProperties, {'version_count__gt': 3}),
    ])
    def test_cached_annotation_value(self, model, filters):
        # Filter both before and after the select_properties call to check if
        # the annotation gets selected correctly regardless
        queryset = model.objects.filter(**filters).select_properties('version_count', 'major_sum').filter(**filters)
        assert 'version_count' in queryset.query.annotations
        assert 'major_sum' in queryset.query.annotations
        assert all(model.version_count._has_cached_value(obj) for obj in queryset)
        assert all(model.major_sum._has_cached_value(obj) for obj in queryset)

    @pytest.mark.parametrize('model, limit, expected_total', [
        (ApplicationWithClassBasedProperties, None, 8),
        (ApplicationWithClassBasedProperties, 1, 4),
        (ApplicationWithDecoratorBasedProperties, None, 8),
        (ApplicationWithDecoratorBasedProperties, 1, 4),
    ])
    def test_aggregate_based_on_queryable_property(self, model, limit, expected_total):
        result = model.objects.all()[:limit].aggregate(total_version_count=models.Sum('version_count'))
        assert result['total_version_count'] == expected_total

    @pytest.mark.parametrize('model, limit, expected_total', [
        (VersionWithClassBasedProperties, None, 32),
        (VersionWithClassBasedProperties, 4, 16),
        (VersionWithDecoratorBasedProperties, None, 32),
        (VersionWithDecoratorBasedProperties, 4, 16),
    ])
    def test_aggregate_based_on_queryable_property_across_relation(self, model, limit, expected_total):
        result = model.objects.all()[:limit].aggregate(total_version_count=models.Sum('application__version_count'))
        assert result['total_version_count'] == expected_total

    @pytest.mark.parametrize('model', [ApplicationWithClassBasedProperties, ApplicationWithDecoratorBasedProperties])
    def test_iterator(self, model):
        queryset = model.objects.filter(version_count=4).select_properties('version_count')
        for application in queryset.iterator():
            assert model.version_count._has_cached_value(application)
            assert application.version_count == 4
        assert queryset._result_cache is None

    @pytest.mark.parametrize('model', [ApplicationWithClassBasedProperties, ApplicationWithDecoratorBasedProperties])
    def test_removed_annotation(self, model):
        """
        Test that queries can still be performed even if queryable property annotations have been manually removed from
        the queryset.
        """
        queryset = model.objects.select_properties('version_count')
        del queryset.query.annotations['version_count']
        assert bool(queryset)
        assert all(not model.version_count._has_cached_value(obj) for obj in queryset)


@pytest.mark.skipif(DJANGO_VERSION < (1, 8), reason="Expression-based annotations didn't exist before Django 1.8")
class TestExpressionAnnotations(object):

    @pytest.mark.parametrize('model, filters', [
        (VersionWithClassBasedProperties, {}),
        (VersionWithDecoratorBasedProperties, {}),
        (VersionWithClassBasedProperties, {'version': '1.2.3'}),
        (VersionWithDecoratorBasedProperties, {'version': '1.2.3'}),
    ])
    def test_cached_annotation_value(self, model, filters):
        # Filter both before and after the select_properties call to check if
        # the annotation gets selected correctly regardless
        queryset = model.objects.filter(**filters).select_properties('version').filter(**filters)
        assert 'version' in queryset.query.annotations
        assert all(model.version._has_cached_value(obj) for obj in queryset)

    @pytest.mark.parametrize('model, annotation, expected_value', [
        (VersionWithClassBasedProperties, models.F('version'), '{}'),
        (VersionWithDecoratorBasedProperties, models.F('version'), '{}'),
        (VersionWithClassBasedProperties, Concat(Value('V'), 'version'), 'V{}'),
        (VersionWithDecoratorBasedProperties, Concat(Value('V'), 'version'), 'V{}'),
    ])
    def test_annotation_based_on_queryable_property(self, model, annotation, expected_value):
        queryset = model.objects.annotate(annotation=annotation)
        for version in queryset:
            assert version.annotation == expected_value.format(version.version)
            # Check that a property annotation used implicitly by another
            # annotation does not lead to a selection of the property
            # annotation
            assert not model.version._has_cached_value(version)

    @pytest.mark.parametrize('model', [VersionWithClassBasedProperties, VersionWithDecoratorBasedProperties])
    def test_iterator(self, model):
        queryset = model.objects.filter(major_minor='2.0').select_properties('version')
        for version in queryset.iterator():
            assert model.version._has_cached_value(version)
            assert version.version == '2.0.0'
        assert queryset._result_cache is None
