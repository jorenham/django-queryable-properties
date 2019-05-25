# encoding: utf-8

from copy import deepcopy

from django.utils import six, tree

from .exceptions import QueryablePropertyDoesNotExist


def get_queryable_property(model, name):
    """
    Retrieve the :class:`queryable_properties.properties.QueryableProperty`
    object with the given attribute name from the given model class or raise
    an error if no queryable property with that name exists on the model class.

    :param type model: The model class to retrieve the property object from.
    :param str name: The name of the property to retrieve.
    :return: The queryable property.
    :rtype: queryable_properties.properties.QueryableProperty
    """
    from .properties import QueryableProperty

    prop = getattr(model, name, None)
    if not isinstance(prop, QueryableProperty):
        raise QueryablePropertyDoesNotExist("{model} has no queryable property named '{name}'".format(
            model=model.__name__, name=name))
    return prop


get_queryable_property.__safe_for_unpickling__ = True


def reset_queryable_property(obj, name):
    """
    Reset the cached value of the queryable property with the given name on the
    given model instance. Read-accessing the property on this model instance at
    a later point will therefore execute the property's getter again.

    :param django.db.models.Model obj: The model instance to reset the cached
                                       value on.
    :param str name: The name of the queryable property.
    """
    prop = get_queryable_property(obj.__class__, name)
    prop._clear_cached_value(obj)


class InjectableMixin(object):
    """
    A base class for mixin classes that are used to dynamically created classes
    based on a base class and the mixin class.
    """

    # Intentionally use a single cache for all subclasses since it is in no way
    # harmful to use a shared cache.
    _created_classes = {}

    def __init__(self, *args, **kwargs):
        super(InjectableMixin, self).__init__(*args, **kwargs)
        self.init_injected_attrs()

    def init_injected_attrs(self):
        """
        Initialize the attributes this mixin contributes. This method will be
        called during :meth:`__init__` and after the mixin was injected into an
        object.
        """
        pass

    @classmethod
    def mix_with_class(cls, base_class, class_name=None):
        """
        Create a new class based on the given base class and this mixin class.
        The created class will also receive a custom :meth:`__reduce__`
        implementation to make its objects picklable.

        :param type base_class: The base class to mix the mixin into.
        :param str class_name: An optional name for the dynamically created
                               class. If None is supplied (default), the class
                               name of the dynamically created class will be
                               the one of the object's original class.
        :return: The generated class.
        :rtype: type
        """
        class_name = str(class_name or base_class.__name__)
        cache_key = (base_class, cls, class_name)
        created_class = cls._created_classes.get(cache_key)
        if created_class is None:
            # Make sure objects of a dynamically created class can be pickled.
            def __reduce__(self):
                return _unpickle_injected_object, (base_class, cls, class_name), self.__dict__

            created_class = cls._created_classes[cache_key] = type(class_name, (cls, base_class),
                                                                   {'__reduce__': __reduce__})
        return created_class

    @classmethod
    def inject_into_object(cls, obj, class_name=None):
        """
        Update the given object's class by dynamically generating a new class
        based on the object's original class and this mixin class and changing
        the given object into an object of this new class.

        :param obj: The object whose class should be changed.
        :param str class_name: An optional name for the dynamically created
                               class. If None is supplied (default), the class
                               name of the dynamically created class will be
                               the one of the object's original class.
        """
        obj.__class__ = cls.mix_with_class(obj.__class__, class_name)
        obj.init_injected_attrs()


# This must be a standalone function for Python 2, where it could not be
# pickled being a static method on the InjectableMixin, even if the underlying
# function had the __safe_for_unpickling__ flag.
def _unpickle_injected_object(base_class, mixin_class, class_name=None):
    """
    Callable for the pickler to unpickle objects of a dynamically created class
    based on the InjectableMixin. It creates the base object from the original
    base class and re-injects the mixin class when unpickling an object.

    :param type base_class: The base class of the pickled object before adding
                            the mixin via injection.
    :param type mixin_class: The :class:`InjectableMixin` subclass that was
                             injected into the pickled object.
    :param str class_name: The class name of the pickled object's dynamically
                           created class.
    :return: The initial unpickled object (before the pickler restores the
             object's state).
    """
    obj = base_class.__new__(base_class, ())
    mixin_class.inject_into_object(obj, class_name)
    return obj


_unpickle_injected_object.__safe_for_unpickling__ = True


def modify_tree_node(node, func, copy=True):
    """
    Modify a tree node and all of its subnodes using the given transformation
    callable.

    :param tree.Node node: The node to modify.
    :param callable func: A callable that will be called for every encountered
                          actual node value (not subnodes) with that value as
                          its only parameter. It must returned the replacement
                          value for the given value.
    :param bool copy: Whether to create a copy of the original node and modify
                      this copy instead of modifying the original node in
                      place.
    :return: The modified node or node copy.
    :rtype: tree.Node
    """
    if copy:
        node = deepcopy(node)

    for index, child in enumerate(node.children):
        if isinstance(child, tree.Node):
            modify_tree_node(child, func, copy=False)
        else:
            node.children[index] = func(child)

    return node
