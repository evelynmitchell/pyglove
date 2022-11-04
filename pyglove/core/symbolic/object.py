# Copyright 2022 The PyGlove Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Symbolic object."""

import abc
import functools

import typing
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union

from pyglove.core import object_utils
from pyglove.core import typing as pg_typing
from pyglove.core.symbolic import base
from pyglove.core.symbolic import dict as pg_dict
from pyglove.core.symbolic import schema_utils


class ObjectMeta(abc.ABCMeta):
  """Meta class for pg.Object."""

  @property
  def schema(cls) -> pg_typing.Schema:
    """Class level property for schema."""
    return getattr(cls, '__schema__', None)

  @property
  def sym_fields(cls) -> pg_typing.Dict:
    """Gets symbolic field."""
    return getattr(cls, '__sym_fields')

  @property
  def type_name(cls) -> str:
    """Class level property for type name.

    NOTE(daiyip): This is used for serialization/deserialization.

    Returns:
      String of <module>.<class> as identifier.
    """
    return f'{cls.__module__}.{cls.__name__}'

  @property
  def serialization_key(cls) -> str:
    """Gets serialization type key."""
    return getattr(cls, '__serialization_key__')

  @property
  def init_arg_list(cls) -> List[str]:
    """Gets __init__ positional argument list."""
    return cls.schema.metadata['init_arg_list']


# Use ObjectMeta as meta class to inherit schema and type_name property.
class Object(base.Symbolic, metaclass=ObjectMeta):
  """Base class for symbolic user classes.

  PyGlove allow symbolic programming interfaces to be easily added to most
  Python classes in two ways:

  * Developing a dataclass-like symbolic class by subclassing ``pg.Object``.
  * Developing a class as usual and decorate it using :func:`pyglove.symbolize`.
    This also work with existing classes.

  By directly subclassing ``pg.Object``, programmers can create new symbolic
  classes with the least effort. For example::

    @pg.members([
        # Each tuple in the list defines a symbolic field for `__init__`.
        ('name', pg.typing.Str().noneable(), 'Name to greet'),
        ('time_of_day',
        pg.typing.Enum('morning', ['morning', 'afternnon', 'evening']),
        'Time of the day.')
    ])
    class Greeting(pg.Object):

      def __call__(self):
        # Values for symbolic fields can be accessed
        # as public data members of the symbolic object.
        print(f'Good {self.time_of_day}, {self.name}')

    # Create an object of Greeting and invoke it,
    # which shall print 'Good morning, Bob'.
    Greeting('Bob')()

  Symbolic fields can be inherited from the base symbolic class: the fields
  from the base class will be copied to the subclass in their declaration
  order, while the subclass can override the inherited fields with more
  restricted validation rules or different default values. For example::

    @pg.members([
        ('x', pg.typing.Int(max_value=10)),
        ('y', pg.typing.Float(min_value=0))
    ])
    class Foo(pg.Object)
      pass

    @pg.members([
        ('x', pg.typing.Int(min_value=1, default=1)),
        ('z', pg.typing.Str().noneable())
    ])
    class Bar(Foo)
      pass

    # Printing Bar's schema will show that there are 3 parameters defined:
    # x : pg.typing.Int(min_value=1, max_value=10, default=1))
    # y : pg.typing.Float(min_value=0)
    # z : pg.typing.Str().noneable()
    print(Bar.schema)
  """

  # Disable pytype attribute checking.
  _HAS_DYNAMIC_ATTRIBUTES = True

  # Class property that indicates whether to automatically register class
  # for deserialization.
  auto_register = True

  # Class property that indicates whether to allow attribute access on symbolic
  # members.
  allow_symbolic_attribute = True

  # Class property that indicates whether to allow to set or rebind symbolic
  # members by value assginment.
  allow_symbolic_assignment = False

  # Allow symbolic mutation using `rebind`.
  allow_symbolic_mutation = True

  # Class property that indicates whether to use `sym_eq` for `__eq__`,
  # `sym_ne` for `__ne__`, and `sym_hash` for `__hash__`.
  use_symbolic_comparison = True

  @classmethod
  def __init_subclass__(cls):
    super().__init_subclass__()

    # Inherit schema from base classes that have schema
    # in the ordered of inheritance.
    # TODO(daiyip): size of base_schema_list can be reduced
    # by looking at their inheritance chains.
    base_schema_list = []
    for base_cls in cls.__bases__:
      base_schema = getattr(base_cls, 'schema', None)
      if isinstance(base_schema, pg_typing.Schema):
        base_schema_list.append(base_schema)

    cls_schema = schema_utils.formalize_schema(
        pg_typing.create_schema(
            maybe_field_list=[],
            name=cls.type_name,
            base_schema_list=base_schema_list,
            allow_nonconst_keys=True,
            metadata={}))
    setattr(cls, '__schema__', cls_schema)
    setattr(cls, '__sym_fields', pg_typing.Dict(cls_schema))
    setattr(cls, '__serialization_key__', cls.type_name)
    cls_schema.metadata['init_arg_list'] = schema_utils.auto_init_arg_list(cls)
    if cls.auto_register:
      schema_utils.register_cls_for_deserialization(cls, cls.type_name)

    cls._update_init_signature_based_on_schema()
    cls._generate_sym_attributes_if_enabled()

  @classmethod
  def _update_init_signature_based_on_schema(cls):
    """Updates the signature of `__init__` if needed."""
    if (cls.__init__ is not Object.__init__
        and not hasattr(cls.__init__, '__sym_generated_init__')):
      # We only generate `__init__` from pg.Object subclass which does not
      # override the `__init__` method.
      # Functor and ClassWrapper override their `__init__` methods, therefore
      # they need to synchronize the __init__ signature by themselves.
      return
    signature = pg_typing.get_init_signature(
        cls.schema, cls.__module__, '__init__', f'{cls.__name__}.__init__')
    pseudo_init = signature.make_function(['pass'])

    # Create a new `__init__` that passes through all the arguments to
    # in `pg.Object.__init__`. This is needed for each class to use different
    # signature.
    @functools.wraps(pseudo_init)
    def _init(self, *args, **kwargs):
      # We pass through the arguments to `Object.__init__` instead of
      # `super()` since the parent class uses a generated `__init__` will
      # be delegated to `Object.__init__` eventually. Therefore, directly
      # calling `Object.__init__` is equivalent to calling `super().__init__`.
      Object.__init__(self, *args, **kwargs)
    setattr(_init, '__sym_generated_init__', True)
    setattr(cls, '__init__', _init)

  @classmethod
  def _generate_sym_attributes_if_enabled(cls):
    """Generates symbolic attributes based on schema if they are enabled."""
    def _create_sym_attribute(attr_name, field):
      return property(object_utils.make_function(
          attr_name,
          ['self'],
          [f'return self._sym_attributes[\'{attr_name}\']'],
          return_type=field.value.annotation))

    if cls.allow_symbolic_attribute:
      for key, field in cls.schema.fields.items():
        if isinstance(key, pg_typing.ConstStrKey):
          attr_name = str(key)
          if not hasattr(cls, attr_name):
            setattr(cls, attr_name, _create_sym_attribute(attr_name, field))

  @classmethod
  def partial(cls, *args, **kwargs) -> 'Object':
    """Class method that creates a partial object of current class."""
    return cls(*args, allow_partial=True, **kwargs)

  @classmethod
  def from_json(
      cls,
      json_value: Any,
      *,
      allow_partial: bool = False,
      root_path: Optional[object_utils.KeyPath] = None) -> 'Object':
    """Class method that load an symbolic Object from a JSON value.

    Example::

        @pg.members([
          ('f1', pg.typing.Int()),
          ('f2', pg.typing.Dict([
            ('f21', pg.typing.Bool())
          ]))
        ])
        class Foo(pg.Object):
          pass

        foo = Foo.from_json({
            'f1': 1,
            'f2': {
              'f21': True
            }
          })

        # or

        foo2 = symbolic.from_json({
            '_type': '__main__.Foo',
            'f1': 1,
            'f2': {
              'f21': True
            }
        })

        assert foo == foo2

    Args:
      json_value: Input JSON value, only JSON dict is acceptable.
      allow_partial: Whether to allow elements of the list to be partial.
      root_path: KeyPath of loaded object in its object tree.

    Returns:
      A symbolic Object instance.
    """
    return cls(allow_partial=allow_partial, root_path=root_path, **json_value)

  def __init__(
      self,
      *args,
      allow_partial: bool = False,
      sealed: Optional[bool] = None,
      root_path: Optional[object_utils.KeyPath] = None,
      explicit_init: bool = False,
      **kwargs):
    """Create an Object instance.

    Args:
      *args: positional arguments.
      allow_partial: If True, the object can be partial.
      sealed: If True, seal the object from future modification (unless under
        a `pg.seal(False)` context manager). If False, treat the object as
        unsealed. If None, it's determined by `cls.allow_symbolic_mutation`.
      root_path: The symbolic path for current object. By default it's None,
        which indicates that newly constructed object does not have a parent.
      explicit_init: Should set to `True` when `__init__` is called via
        `pg.Object.__init__` instead of `super().__init__`.
      **kwargs: key/value arguments that align with the schema. All required
        keys in the schema must be specified, and values should be acceptable
        according to their value spec.

    Raises:
      KeyError: When required key(s) are missing.
      ValueError: When value(s) are not acceptable by their value spec.
    """
    # Placeholder for Google-internal usage instrumentation.

    if sealed is None:
      sealed = not self.__class__.allow_symbolic_mutation

    if not isinstance(allow_partial, bool):
      raise TypeError(
          f'Expect bool type for argument \'allow_partial\' in '
          f'symbolic.Object.__init__ but encountered {allow_partial}.')

    # We delay the seal attempt until members are all set.
    super().__init__(
        allow_partial=allow_partial,
        accessor_writable=self.__class__.allow_symbolic_assignment,
        sealed=sealed,
        root_path=root_path,
        init_super=not explicit_init)

    # Fill field_args and init_args from **kwargs.
    _, unmatched_keys = self.__class__.schema.resolve(list(kwargs.keys()))
    if unmatched_keys:
      arg_phrase = object_utils.auto_plural(len(unmatched_keys), 'argument')
      keys_str = object_utils.comma_delimited_str(unmatched_keys)
      raise TypeError(
          f'{self.__class__.__name__}.__init__() got unexpected '
          f'keyword {arg_phrase}: {keys_str}')

    field_args = {}
    # Fill field_args and init_args from *args.
    init_arg_names = self.__class__.init_arg_list
    if args:
      if not self.__class__.schema.fields:
        raise TypeError(f'{self.__class__.__name__}() takes no arguments.')
      elif init_arg_names and init_arg_names[-1].startswith('*'):
        vararg_name = init_arg_names[-1][1:]
        vararg_field = self.__class__.schema.get_field(vararg_name)
        assert vararg_field is not None

        num_named_args = len(init_arg_names) - 1
        field_args[vararg_name] = list(args[num_named_args:])
        args = args[:num_named_args]
      elif len(args) > len(init_arg_names):
        arg_phrase = object_utils.auto_plural(len(init_arg_names), 'argument')
        was_phrase = object_utils.auto_plural(len(args), 'was', 'were')
        raise TypeError(
            f'{self.__class__.__name__}.__init__() takes '
            f'{len(init_arg_names)} positional {arg_phrase} but {len(args)} '
            f'{was_phrase} given.')

      for i, arg_value in enumerate(args):
        arg_name = init_arg_names[i]
        field_args[arg_name] = arg_value

    for k, v in kwargs.items():
      if k in field_args:
        values_str = object_utils.comma_delimited_str([field_args[k], v])
        raise TypeError(
            f'{self.__class__.__name__}.__init__() got multiple values for '
            f'argument \'{k}\': {values_str}.')
      field_args[k] = v

    # Check missing arguments when partial binding is disallowed.
    if not base.accepts_partial(self):
      missing_args = []
      for field in self.__class__.schema.fields.values():
        if (not field.value.has_default
            and isinstance(field.key, pg_typing.ConstStrKey)
            and field.key not in field_args):
          missing_args.append(str(field.key))
      if missing_args:
        arg_phrase = object_utils.auto_plural(len(missing_args), 'argument')
        keys_str = object_utils.comma_delimited_str(missing_args)
        raise TypeError(
            f'{self.__class__.__name__}.__init__() missing {len(missing_args)} '
            f'required {arg_phrase}: {keys_str}.')

    self._set_raw_attr('_sym_attributes', pg_dict.Dict(
        field_args,
        value_spec=self.__class__.sym_fields,
        allow_partial=allow_partial,
        sealed=sealed,
        accessor_writable=self.__class__.allow_symbolic_assignment,
        root_path=root_path,
        pass_through_parent=True))
    self._sym_attributes.sym_setparent(self)
    self._on_init()
    self.seal(sealed)

  #
  # Events that subclasses can override.
  #

  def _on_init(self):
    """Event that is triggered at then end of __init__."""
    self._on_bound()

  def _on_bound(self) -> None:
    """Event that is triggered when any value in the subtree are set/updated.

    NOTE(daiyip): This is the best place to set derived members from members
    registered by the schema. It's called when any value in the sub-tree is
    modified, thus making sure derived members are up-to-date.

    When derived members are expensive to create/update, you can implement
    _init, _on_rebound, _on_subtree_rebound to update derived members only when
    they are impacted.

    _on_bound is not called on per-field basis, it's called at most once
    during a rebind call (though many fields may be updated)
    and during __init__.
    """

  def _on_change(self,
                 field_updates: Dict[object_utils.KeyPath, base.FieldUpdate]):
    """Event that is triggered when field values in the subtree are updated.

    This event will be called
      * On per-field basis when object is modified via attribute.
      * In batch when multiple fields are modified via `rebind` method.

    When a field in an object tree is updated, all ancestors' `_on_change` event
    will be triggered in order, from the nearest one to furthest one.

    Args:
      field_updates: Updates made to the subtree. Key path is relative to
        current object.

    Returns:
      it will call `_on_bound` and return the return value of `_on_bound`.
    """
    del field_updates
    return self._on_bound()

  def _on_path_change(
      self, old_path: object_utils.KeyPath, new_path: object_utils.KeyPath):
    """Event that is triggered after the symbolic path changes."""
    del old_path, new_path

  def _on_parent_change(
      self,
      old_parent: Optional[base.Symbolic],
      new_parent: Optional[base.Symbolic]):
    """Event that is triggered after the symbolic parent changes."""
    del old_parent, new_parent

  @property
  def sym_init_args(self) -> pg_dict.Dict:
    """Returns symbolic attributes which are the arguments for `__init__`."""
    return self._sym_attributes

  def sym_hasattr(self, key: Union[str, int]) -> bool:
    """Tests if a symbolic attribute exists."""
    if key == '_sym_attributes':
      raise ValueError(
          f'{self.__class__.__name__}.__init__ should call `super().__init__`.')
    return (isinstance(key, str)
            and not key.startswith('_') and key in self._sym_attributes)

  def sym_attr_field(
      self, key: Union[str, int]
      ) -> Optional[pg_typing.Field]:
    """Returns the field definition for a symbolic attribute."""
    return self._sym_attributes.sym_attr_field(key)

  def sym_keys(self) -> Iterator[str]:
    """Iterates the keys of symbolic attributes."""
    return self._sym_attributes.sym_keys()

  def sym_values(self):
    """Iterates the values of symbolic attributes."""
    return self._sym_attributes.sym_values()

  def sym_items(self):
    """Iterates the (key, value) pairs of symbolic attributes."""
    return self._sym_attributes.sym_items()

  def sym_eq(self, other: Any) -> bool:
    """Tests symbolic equality."""
    return self is other or (
        type(self) is type(other) and base.eq(
            self._sym_attributes, other._sym_attributes))   # pylint: disable=protected-access

  def sym_lt(self, other: Any) -> bool:
    """Tests symbolic less-than."""
    if type(self) is not type(other):
      return base.lt(self, other)
    return base.lt(self._sym_attributes, other._sym_attributes)  # pylint: disable=protected-access

  def sym_hash(self) -> int:
    """Symbolically hashing."""
    return base.sym_hash((self.__class__, base.sym_hash(self._sym_attributes)))

  def sym_setparent(self, parent: base.Symbolic):
    """Sets the parent of current node in the symbolic tree."""
    old_parent = self.sym_parent
    super().sym_setparent(parent)
    if old_parent is not parent:
      self._on_parent_change(old_parent, parent)

  def _sym_getattr(  # pytype: disable=signature-mismatch  # overriding-parameter-type-checks
      self, key: str) -> Any:
    """Get symbolic field by key."""
    return self._sym_attributes[key]

  def _sym_rebind(
      self, path_value_pairs: Dict[object_utils.KeyPath, Any]
      ) -> List[base.FieldUpdate]:
    """Rebind current object using object-form members."""
    if base.treats_as_sealed(self):
      raise base.WritePermissionError(
          f'Cannot rebind a sealed {self.__class__.__name__}.')
    return self._sym_attributes._sym_rebind(path_value_pairs)  # pylint: disable=protected-access

  def _sym_clone(self, deep: bool, memo: Any = None) -> 'Object':
    """Copy flags."""
    kwargs = dict()
    for k, v in self._sym_attributes.items():
      if deep or isinstance(v, base.Symbolic):
        v = base.clone(v, deep, memo)
      kwargs[k] = v
    return self.__class__(allow_partial=self._allow_partial,
                          sealed=self._sealed,
                          **kwargs)  # pytype: disable=not-instantiable

  def _sym_missing(self) -> Dict[str, Any]:
    """Returns missing values."""
    return self._sym_attributes.sym_missing(flatten=False)

  def _sym_nondefault(self) -> Dict[str, Any]:
    """Returns non-default values."""
    return self._sym_attributes.sym_nondefault(flatten=False)

  def set_accessor_writable(self, writable: bool = True) -> 'Object':
    """Sets accessor writable."""
    self._sym_attributes.set_accessor_writable(writable)
    super().set_accessor_writable(writable)
    return self

  def seal(self, sealed: bool = True) -> 'Object':
    """Seal or unseal current object from further modification."""
    self._sym_attributes.seal(sealed)
    super().seal(sealed)
    return self

  def _update_children_paths(
      self,
      old_path: object_utils.KeyPath,
      new_path: object_utils.KeyPath) -> None:
    """Update children paths according to root_path of current node."""
    self._sym_attributes.sym_setpath(new_path)
    self._on_path_change(old_path, new_path)

  def _set_item_without_permission_check(  # pytype: disable=signature-mismatch  # overriding-parameter-type-checks
      self, key: str, value: Any) -> Optional[base.FieldUpdate]:
    """Set item without permission check."""
    return self._sym_attributes._set_item_without_permission_check(key, value)  # pylint: disable=protected-access

  @property
  def _subscribes_field_updates(self) -> bool:
    """Returns True if current object subscribes field updates.

    For pg.Object, this return True only when _on_change is overridden
    from subclass.
    """
    return self._on_change.__code__ is not Object._on_change.__code__  # pytype: disable=attribute-error

  def __setattr__(self, name: str, value: Any) -> None:
    """Set field value by attribute."""
    # NOTE(daiyip): two types of members are treated as regular members:
    # 1) All private members which prefixed with '_'.
    # 2) Public members that are not declared as symbolic members.
    if (not self.allow_symbolic_attribute
        or not self.__class__.schema.get_field(name)
        or name.startswith('_')):
      super().__setattr__(name, value)
    else:
      if base.treats_as_sealed(self):
        raise base.WritePermissionError(
            self._error_message(
                f'Cannot set attribute {name!r}: object is sealed.'))
      if not base.writtable_via_accessors(self):
        raise base.WritePermissionError(
            self._error_message(
                f'Cannot set attribute of <class {self.__class__.__name__}> '
                f'while `{self.__class__.__name__}.allow_symbolic_assignment` '
                f'is set to False or under `pg.as_sealed` context.'))
      self._sym_attributes[name] = value

  def __getattribute__(self, name: str) -> Any:
    """Override to accomondate symbolic attributes with variable keys."""
    try:
      return super().__getattribute__(name)
    except AttributeError as error:
      if not self.allow_symbolic_attribute or not self.sym_hasattr(name):
        raise error
      return self._sym_getattr(name)

  def __eq__(self, other: Any) -> bool:
    """Operator==."""
    if self.use_symbolic_comparison:
      return self.sym_eq(other)
    # NOTE(daiyip): In Python2, not all classes (e.g. int) have `__eq__` method.
    # Therefore we always check the presence of `__eq__` and use it when
    # possible. Otherwise we downgrade the default behavior by returning
    # `NotImplemented`.
    if hasattr(super(), '__eq__'):
      return super().__eq__(other)

    # In Python, `__eq__` may returns NotImplemented to fallback to the
    # default equality check. Reference:
    # https://stackoverflow.com/questions/40780004/returning-notimplemented-from-eq
    return NotImplemented

  def __ne__(self, other: Any) -> bool:
    """Operator!=."""
    r = self.__eq__(other)
    if r is NotImplemented:
      return r
    return not r

  def __hash__(self) -> int:
    """Hashing function."""
    if self.use_symbolic_comparison:
      return self.sym_hash()
    return super().__hash__()

  def sym_jsonify(self, **kwargs) -> object_utils.JSONValueType:
    """Converts current object to a dict of plain Python objects."""
    return object_utils.merge([{
        base._TYPE_NAME_KEY: self.__class__.serialization_key    # pylint: disable=protected-access
    }, self._sym_attributes.to_json(**kwargs)])

  def format(self,
             compact: bool = False,
             verbose: bool = False,
             root_indent: int = 0,
             **kwargs) -> str:
    """Formats this object."""
    return self._sym_attributes.format(
        compact,
        verbose,
        root_indent,
        cls_name=self.__class__.__name__,
        bracket_type=object_utils.BracketType.ROUND,
        **kwargs)


base.Symbolic.ObjectType = Object


def members(
    fields: List[Union[
        pg_typing.Field,
        Tuple[Union[str, pg_typing.KeySpec], pg_typing.ValueSpec, str],
        Tuple[Union[str, pg_typing.KeySpec], pg_typing.ValueSpec, str, Any]]],
    metadata: Optional[Dict[str, Any]] = None,
    init_arg_list: Optional[Sequence[str]] = None,
    **kwargs
) -> pg_typing.Decorator:
  """Function/Decorator for declaring symbolic fields for ``pg.Object``.

  Example::

    @pg.members([
      # Declare symbolic fields. Each field produces a symbolic attribute
      # for its object, which can be accessed by `self.<field_name>`.
      # Description is optional.
      ('x', pg.typing.Int(min_value=0, default=0), 'Description for `x`.'),
      ('y', pg.typing.Str(), 'Description for `y`.')
    ])
    class A(pg.Object):
      def sum(self):
        return self.x + self.y

    @pg.members([
      # Override field 'x' inherited from class A and make it more restrictive.
      ('x', pg.typing.Int(max_value=10, default=5)),
      # Add field 'z'.
      ('z', pg.typing.Bool().noneable())
    ])
    class B(A):
      pass

    @pg.members([
      # Declare dynamic fields: any keyword can be acceptable during `__init__`
      # and can be accessed using `self.<field_name>`.
      (pg.typing.StrKey(), pg.typing.Int())
    ])
    class D(B):
      pass

    @pg.members([
      # Declare dynamic fields: keywords started with 'foo' is acceptable.
      (pg.typing.StrKey('foo.*'), pg.typing.Int())
    ])
    class E(pg.Object):
      pass

  See :class:`pyglove.typing.ValueSpec` for supported value specifications.

  Args:
    fields: A list of pg.typing.Field or equivalent tuple representation as
      (<key>, <value-spec>, [description], [metadata-objects]). `key` should be
      a string. `value-spec` should be pg_typing.ValueSpec classes or
      equivalent, e.g. primitive values which will be converted to ValueSpec
      implementation according to its type and used as its default value.
      `description` is optional only when field overrides a field from its
      parent class. `metadata-objects` is an optional list of any type, which
      can be used to generate code according to the schema.
    metadata: Optional dict of user objects as class-level metadata which will
      be attached to class schema.
    init_arg_list: An optional sequence of strings as the positional argument
      list for `__init__`. This is helpful when symbolic attributes are
      inherited from base classes or the user want to change its order.
      If not provided, the `init_arg_list` will be automatically generated
      from symbolic attributes defined from ``pg.members`` in their declaration
      order, from the base classes to the subclass.
    **kwargs: Keyword arguments for infrequently used options.
      Acceptable keywords are:

      * `serialization_key`: An optional string to be used as the serialization
        key for the class during `sym_jsonify`. If None, `cls.type_name` will be
        used. This is introduced for scenarios when we want to relocate a class,
        before the downstream can recognize the new location, we need the class
        to serialize it using previous key.
      * `additional_keys`: An optional list of strings as additional keys to
        deserialize an object of the registered class. This can be useful
        when we need to relocate or rename the registered class while being able
        to load existing serialized JSON values.

  Returns:
    a decorator function that register the class or function with schema
      created from the fields.

  Raises:
    TypeError: Decorator cannot be applied on target class or keyword argument
      provided is not supported.
    KeyError: If type has already been registered in the registry.
    ValueError: schema cannot be created from fields.
  """
  serialization_key = kwargs.pop('serialization_key', None)
  additional_keys = kwargs.pop('additional_keys', None)
  if kwargs:
    raise TypeError(f'Unsupported keyword arguments: {list(kwargs.keys())!r}.')

  def _decorator(cls):
    """Decorator function that registers schema with an Object class."""
    schema_utils.update_schema(
        cls,
        fields,
        metadata=metadata,
        init_arg_list=init_arg_list,
        serialization_key=serialization_key,
        additional_keys=additional_keys)
    return cls
  return typing.cast(pg_typing.Decorator, _decorator)


# Create an alias method for members.
schema = members

