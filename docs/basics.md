# Basics

## Ways to define queryable properties

There are two ways to implement a queryable property:
- Using decorated methods directly on the model class (just like regular properties)
- Implementing the queryable property as a class and using its instances as class attributes on the model class (much
  like model fields)

Say we'd want to implement a queryable property for the `ApplicationVersion` example model that simply returns the
combined version information as a string.
To implement only the getter and the setter of such queryable property, the two following code examples achieve the
same result.

Using the decorator-based approach (looks just like a regular property):
```python
from queryable_properties import queryable_property


class ApplicationVersion(models.Model):
    ...
    
    @queryable_property
    def version_str(self):
        return '{major}.{minor}'.format(major=self.major, minor=self.minor)
        
    @version_str.setter
    def version_str(self, value):
        # Don't implement any validation to keep the example simple.
        self.major, self.minor = value.split('.')
```

Using the class-based approach:
```python
from queryable_properties import QueryableProperty, SetterMixin


class VersionStringProperty(SetterMixin, QueryableProperty):

    def get_value(self, obj):
        return '{major}.{minor}'.format(major=obj.major, minor=obj.minor)
    
    def set_value(self, obj, value):
        # Don't implement any validation to keep the example simple.
        obj.major, obj.minor = value.split('.')
        

class ApplicationVersion(models.Model):
    ...
    
    version_str = VersionStringProperty()
```

The following chapters of this documentation will show all available decorators, mixins and implementable methods in
detail.

### When to use which approach

It all depends on your needs and preferences, but a general rule of thumb is using the class-based approach to 
implement re-usable queryable properties or to be able to use inheritance.
It would also be pretty easy to write parameterizable property classes by adding parameters to their `__init__`
methods.

Class-based implementations come, however, with the small disadvantage of having to define the property's logic outside
of the actual model class (unlike regular property implementations).
It would therefore probably be preferable to use the decorator-based approach for unique, non-reusable implementations.

## Using the required manager/queryset

If we were to actually implement queryset-related logic in the examples above, the `ApplicationVersion` model would be
missing one small detail to actually be able to use the queryable properties in querysets: the model must use a special
queryset class, which can most easily be achieved by using a special manager:

```python
from queryable_properties.managers import QueryablePropertiesManager


class ApplicationVersion(models.Model):
    ...
    
    objects = QueryablePropertiesManager()
```

This manager allows to use the queryable properties in querysets created by this manager (e.g. via
`ApplicationVersion.objects.all()`).
If there's a need to use another special queryset class, `queryable_properties` also comes with a mixin to add its
logic to other custom querysets: `queryable_properties.managers.QueryablePropertiesQuerySetMixin`.
A manager class can then be generated from the queryset class using `CustomQuerySet.as_manager()` or
`CustomManager.from_queryset(CustomQuerySet)`.

```eval_rst
.. note::
   The queryset features of queryable properties can only be used on querysets that execute queries for the model they
   are defined on (and if the required queryset class is used). Queryable properties cannot be used in querysets
   across relations (so even if the ``version_str`` property above implemented filtering in querysets, it would still
   **not** be possible to use it across relations like ``Application.objects.filter(versions__version_str='2.0')``).
   
   This feature may be added in the future if it can be implemented with reasonable effort.
```