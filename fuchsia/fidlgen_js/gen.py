#!/usr/bin/env python

# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import fidl
import json


class _CompoundIdentifier(object):

  def __init__(self, library, name):
    self.library = library
    self.name = name


def _ParseLibraryName(lib):
  return lib.split('.')


def _ParseCompoundIdentifier(ident):
  parts = ident.split('/', 2)
  raw_library = ''
  raw_name = parts[0]
  if len(parts) == 2:
    raw_library, raw_name = parts
  library = _ParseLibraryName(raw_library)
  return _CompoundIdentifier(library, raw_name)


def _ChangeIfReserved(name):
  # TODO(crbug.com/883496): Remap any JS keywords.
  return name


def _CompileCompoundIdentifier(compound, ext=''):
  result = _ChangeIfReserved(compound.name) + ext
  return result


def _CompileIdentifier(ident):
  return _ChangeIfReserved(ident)


def _JsTypeForPrimitiveType(t):
  mapping = {
      fidl.IntegerType.INT16: 'number',
      fidl.IntegerType.INT32: 'number',
      fidl.IntegerType.INT64: 'BigInt',
      fidl.IntegerType.INT8: 'number',
      fidl.IntegerType.UINT16: 'number',
      fidl.IntegerType.UINT32: 'number',
      fidl.IntegerType.UINT64: 'BigInt',
      fidl.IntegerType.UINT8: 'number',
  }
  return mapping[t]


def _CompileConstant(val, assignment_type):
  """|assignment_type| is the TypeClass to which |val| will be assigned. This is
  is currently used to scope identifiers to their enum."""
  if val.kind == fidl.ConstantKind.IDENTIFIER:
    if not assignment_type:
      raise Exception('Need assignment_type for IDENTIFIER constant')
    type_compound = _ParseCompoundIdentifier(assignment_type.identifier)
    type_name = _CompileCompoundIdentifier(type_compound)
    val_compound = _ParseCompoundIdentifier(val.identifier)
    return type_name + '.' + val_compound.name
  elif val.kind == fidl.ConstantKind.LITERAL:
    return _CompileLiteral(val.literal)
  else:
    raise Exception('unexpected kind')


def _CompileLiteral(val):
  if val.kind == fidl.LiteralKind.STRING:
    # TODO(crbug.com/883496): This needs to encode the string in an escaped
    # form suitable to JS. Currently using the escaped Python representation,
    # which is passably compatible, but surely has differences in edge cases.
    return repr(val.value)
  elif val.kind == fidl.LiteralKind.NUMERIC:
    return val.value
  elif val.kind == fidl.LiteralKind.TRUE:
    return 'true'
  elif val.kind == fidl.LiteralKind.FALSE:
    return 'false'
  elif val.kind == fidl.LiteralKind.DEFAULT:
    return 'default'
  else:
    raise Exception('unexpected kind')


class Compiler(object):

  def __init__(self, fidl, output_file):
    self.fidl = fidl
    self.f = output_file
    self.output_deferred_to_eof = ''
    self.type_table_defined = set()
    self.type_inline_size_by_name = {}

  def Compile(self):
    self._EmitHeader()
    for c in self.fidl.const_declarations:
      self._CompileConst(c)
    for e in self.fidl.enum_declarations:
      self._CompileEnum(e)
    for u in self.fidl.union_declarations:
      self._CompileUnion(u)
    for s in self.fidl.struct_declarations:
      self._CompileStruct(s)
    for i in self.fidl.interface_declarations:
      self._CompileInterface(i)

    self.f.write(self.output_deferred_to_eof)

  def _InlineSizeOfType(self, t):
    if t.kind == fidl.TypeKind.PRIMITIVE:
      return {
          'int16': 2,
          'int32': 4,
          'int64': 8,
          'int8': 1,
          'uint16': 2,
          'uint32': 4,
          'uint64': 8,
          'uint8': 1,
      }[t.subtype]
    elif t.kind == fidl.TypeKind.STRING:
      return 16
    elif t.kind == fidl.TypeKind.IDENTIFIER:
      size = self.type_inline_size_by_name.get(t.identifier)
      if size is None:
        raise Exception('expected ' + t.identifier +
                        ' to be in self.type_inline_size_by_name')
      return size
    else:
      raise NotImplementedError(t.kind)

  def _EmitHeader(self):
    self.f.write('''// Copyright 2018 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.
//
// WARNING: This file is machine generated by fidlgen_js.

''')

  def _CompileConst(self, const):
    compound = _ParseCompoundIdentifier(const.name)
    name = _CompileCompoundIdentifier(compound)
    value = _CompileConstant(const.value, None)
    self.f.write('''/**
 * @const
 */
const %(name)s = %(value)s;

''' % {
        'name': name,
        'value': value
    })

  def _CompileEnum(self, enum):
    compound = _ParseCompoundIdentifier(enum.name)
    name = _CompileCompoundIdentifier(compound)
    js_type = _JsTypeForPrimitiveType(enum.type)
    data = {'js_type': js_type, 'type': enum.type.value, 'name': name}
    self.f.write('''/**
 * @enum {%(js_type)s}
 */
const %(name)s = {
''' % data)
    for member in enum.members:
      self.f.write('''  %s: %s,\n''' % (member.name,
                                        _CompileConstant(member.value, None)))
    self.f.write('};\n')
    self.f.write('const _kTT_%(name)s = _kTT_%(type)s;\n\n' % data)

  def _CompileUnion(self, union):
    self.type_inline_size_by_name[union.name] = union.size
    compound = _ParseCompoundIdentifier(union.name)
    name = _CompileCompoundIdentifier(compound)
    member_names = []
    enc_cases = []
    dec_cases = []
    for i, m in enumerate(union.members):
      member_name = _ChangeIfReserved(m.name)
      member_names.append(member_name)
      member_type = self._CompileType(m.type)
      enc_cases.append('''\
      case %(index)s:
        _kTT_%(member_type)s.enc(e, o + 4, v.%(member_name)s);
        break;''' % {
          'index': i,
          'member_type': member_type,
          'member_name': member_name,
      })
      dec_cases.append('''\
      case %(index)s:
        result.set_%(member_name)s(_kTT_%(member_type)s.dec(d, o + 4));
        break;''' % {
          'index': i,
          'member_type': member_type,
          'member_name': member_name,
      })

    self.f.write(
        '''\
const _kTT_%(name)s = {
  enc: function(e, o, v) {
    if (v.$tag === $fidl__kInvalidUnionTag) throw "invalid tag";
    e.data.setUint32(o, v.$tag, $fidl__kLE);
    switch (v.$tag) {
%(enc_cases)s
    }
  },
  dec: function(d, o) {
    var tag = d.data.getUint32(o, $fidl__kLE);
    var result = new %(name)s();
    switch (tag) {
%(dec_cases)s
      default:
        throw "invalid tag";
    }
    return result;
  },
};

const _kTT_%(name)s_Nullable = {
  enc: function(e, o, v) {
    e.data.setUint32(o, v ? 0xffffffff : 0, $fidl__kLE);
    e.data.setUint32(o + 4, v ? 0xffffffff : 0, $fidl__kLE);
    var start = e.alloc(%(size)s);
    _kTT_%(name)s.enc(e, start, v);
  },
  dec: function(d, o) {
    if (d.data.getUint32(o, $fidl__kLE) === 0) {
      return new %(name)s();
    }
    var pointer = d.data.getUint32(o + 4, $fidl__kLE);
    var dataOffset = d.claimMemory(%(size)s);
    return _kTT_%(name)s.dec(d, dataOffset);
  },
};

/**
 * @constructor
 */
function %(name)s() { this.reset(); }

%(name)s.prototype.reset = function(i) {
  this.$tag = (i === undefined) ? $fidl__kInvalidUnionTag : i;
''' % {
            'name': name,
            'size': union.size,
            'enc_cases': '\n'.join(enc_cases),
            'dec_cases': '\n'.join(dec_cases),
        })
    for m in member_names:
      self.f.write('  this.%s = null;\n' % m)
    self.f.write('}\n\n')

    for i, m in enumerate(member_names):
      self.f.write('''\
%(name)s.prototype.set_%(member_name)s = function(v) {
  this.reset(%(index)s);
  this.%(member_name)s = v;
};

%(name)s.prototype.is_%(member_name)s = function() {
  return this.$tag === %(index)s;
};

''' % {
          'name': name,
          'member_name': m,
          'index': i,
      })

  def _CompileStruct(self, struct):
    self.type_inline_size_by_name[struct.name] = struct.size
    compound = _ParseCompoundIdentifier(struct.name)
    name = _CompileCompoundIdentifier(compound)
    param_names = [_ChangeIfReserved(x.name) for x in struct.members]
    # TODO(crbug.com/883496): @param and types.
    self.f.write('''/**
 * @constructor
 * @struct
 */
function %(name)s(%(param_names)s) {
''' % {
        'name': name,
        'param_names': ', '.join(param_names)
    })
    for member in struct.members:
      member_name = _ChangeIfReserved(member.name)
      value = '%(member_name)s'
      if member.maybe_default_value:
        value = ('(%(member_name)s !== undefined) ? %(member_name)s : ' +
                 _CompileConstant(member.maybe_default_value, member.type))
      elif self.fidl.declarations.get(member.type.identifier) == \
          fidl.DeclarationsMap.UNION:
        union_compound = _ParseCompoundIdentifier(member.type.identifier)
        union_name = _CompileCompoundIdentifier(union_compound)
        value = ('(%(member_name)s !== undefined) ? %(member_name)s : ' + 'new '
                 + union_name + '()')
      self.f.write(('  this.%(member_name)s = ' + value + ';\n') %
                   {'member_name': member_name})
    self.f.write('}\n\n')

    self.f.write('''const _kTT_%(name)s = {
  enc: function(e, o, v) {
''' % {'name': name})

    for member in struct.members:
      element_ttname = self._CompileType(member.type)
      self.f.write(
          '    _kTT_%(element_ttname)s.enc('
          'e, o + %(offset)s, v.%(member_name)s);\n' % {
              'element_ttname': element_ttname,
              'offset': member.offset,
              'member_name': _ChangeIfReserved(member.name)
          })

    self.f.write('''  },
  dec: function(d, o) {
''')

    for member in struct.members:
      element_ttname = self._CompileType(member.type)
      self.f.write(
          '    var $temp_%(member_name)s = _kTT_%(element_ttname)s.dec('
          'd, o + %(offset)s);\n' % {
              'element_ttname': element_ttname,
              'offset': member.offset,
              'member_name': _ChangeIfReserved(member.name)
          })
    self.f.write('''    return new %(name)s(%(temp_names)s);
  }
};

''' % {
        'name': name,
        'temp_names': ', '.join(['$temp_' + x for x in param_names])
    })

  def _CompileType(self, t):
    """Ensures there's a type table for the given type, and returns the stem of
    its name."""
    if t.kind == fidl.TypeKind.PRIMITIVE:
      return t.subtype
    elif t.kind == fidl.TypeKind.STRING:
      return 'String' + ('_Nullable' if t.nullable else '')
    elif t.kind == fidl.TypeKind.IDENTIFIER:
      compound = _ParseCompoundIdentifier(t.identifier)
      name = _CompileCompoundIdentifier(compound)
      return name + ('_Nullable' if t.nullable else '')
    elif t.kind == fidl.TypeKind.HANDLE:
      return 'Handle'
    elif t.kind == fidl.TypeKind.ARRAY:
      element_ttname = self._CompileType(t.element_type)
      ttname = 'ARR_%d_%s' % (t.element_count, element_ttname)
      if ttname not in self.type_table_defined:
        self.type_table_defined.add(ttname)
        self.output_deferred_to_eof += ('''\
const _kTT_%(ttname)s = {
  enc: function(e, o, v) {
    for (var i = 0; i < %(element_count)s; i++) {
      _kTT_%(element_ttname)s.enc(e, o + (i * %(element_size)s), v[i]);
    }
  },
  dec: function(d, o) {
    var result = [];
    for (var i = 0; i < %(element_count)s; i++) {
      result.push(_kTT_%(element_ttname)s.dec(d, o + (i * %(element_size)s)));
    }
    return result;
  },
};

''' % {
            'ttname': ttname,
            'element_ttname': element_ttname,
            'element_count': t.element_count,
            'element_size': self._InlineSizeOfType(t.element_type),
        })
      return ttname
    elif t.kind == fidl.TypeKind.VECTOR:
      element_ttname = self._CompileType(t.element_type)
      ttname = ('VEC_' + ('Nullable_' if t.nullable else '') + element_ttname)
      pointer_set = '''    if (v === null || v === undefined) {
      e.data.setUint32(o + 8, 0, $fidl__kLE);
      e.data.setUint32(o + 12, 0, $fidl__kLE);
    } else {
      e.data.setUint32(o + 8, 0xffffffff, $fidl__kLE);
      e.data.setUint32(o + 12, 0xffffffff, $fidl__kLE);
    }'''
      if not t.nullable:
        throw_if_null_enc = ('if (v === null || v === undefined) '
                             'throw "non-null vector required";')
        throw_if_null_dec = ('if (pointer === 0) '
                             'throw "non-null vector required";')
        pointer_set = '''    e.data.setUint32(o + 8, 0xffffffff, $fidl__kLE);
    e.data.setUint32(o + 12, 0xffffffff, $fidl__kLE);'''

      if ttname not in self.type_table_defined:
        self.type_table_defined.add(ttname)
        self.output_deferred_to_eof += ('''\
const _kTT_%(ttname)s = {
  enc: function(e, o, v) {
    %(throw_if_null_enc)s
    e.data.setUint32(o, v.length, $fidl__kLE);
    e.data.setUint32(o + 4, 0, $fidl__kLE);
%(pointer_set)s
    var start = e.alloc(v.length * %(element_size)s);
    for (var i = 0; i < v.length; i++) {
      _kTT_%(element_ttname)s.enc(e, start + (i * %(element_size)s), v[i]);
    }
  },
  dec: function(d, o) {
    var len = d.data.getUint32(o, $fidl__kLE);
    var pointer = d.data.getUint32(o + 8, $fidl__kLE);
    %(throw_if_null_dec)s
    var dataOffset = d.claimMemory(len * %(element_size)s);
    var result = [];
    for (var i = 0; i < len; i++) {
      result.push(_kTT_%(element_ttname)s.dec(
          d, dataOffset + (i * %(element_size)s)));
    }
    return result;
  }
};

''' % {
            'ttname': ttname,
            'element_ttname': element_ttname,
            'element_size': self._InlineSizeOfType(t.element_type),
            'pointer_set': pointer_set,
            'throw_if_null_enc': throw_if_null_enc,
            'throw_if_null_dec': throw_if_null_dec
        })
      return ttname
    else:
      raise NotImplementedError(t.kind)

  def _GenerateJsInterfaceForInterface(self, name, interface):
    """Generates a JS @interface for the given FIDL interface."""
    self.f.write('''/**
 * @interface
 */
function %(name)s() {}

''' % {'name': name})

    # Define a JS interface part for the interface for typechecking.
    for method in interface.methods:
      method_name = _CompileIdentifier(method.name)
      if method.has_request:
        param_names = [_CompileIdentifier(x.name) for x in method.maybe_request]
        if len(param_names):
          self.f.write('/**\n')
          # TODO(crbug.com/883496): Emit @param and @return type comments.
          self.f.write(' */\n')
        self.f.write(
            '%(name)s.prototype.%(method_name)s = '
            'function(%(param_names)s) {};\n\n' % {
                'name': name,
                'method_name': method_name,
                'param_names': ', '.join(param_names)
            })

    # Emit message ordinals for later use.
    for method in interface.methods:
      method_name = _CompileIdentifier(method.name)
      self.f.write(
          'const _k%(name)s_%(method_name)s_Ordinal = %(ordinal)s;\n' % {
              'name': name,
              'method_name': method_name,
              'ordinal': method.ordinal
          })

    self.f.write('\n')

  def _GenerateJsProxyForInterface(self, name, interface):
    """Generates the JS side implementation of a proxy class implementing the
    given interface."""
    proxy_name = name + 'Proxy'
    self.f.write('''/**
 * @constructor
 * @implements %(name)s
 */
function %(proxy_name)s() {
  this.channel = $ZX_HANDLE_INVALID;
}

%(proxy_name)s.prototype.$bind = function(channel) {
  this.channel = channel;
};

''' % {
        'name': name,
        'proxy_name': proxy_name
    })
    for method in interface.methods:
      method_name = _CompileIdentifier(method.name)
      if method.has_request:
        type_tables = []
        for param in method.maybe_request:
          type_tables.append(self._CompileType(param.type))
        param_names = [_CompileIdentifier(x.name) for x in method.maybe_request]
        self.f.write(
            '''\
%(proxy_name)s.prototype.%(method_name)s = function(%(param_names)s) {
  if (this.channel === $ZX_HANDLE_INVALID) {
    throw "channel closed";
  }
  var $encoder = new $fidl_Encoder(_k%(name)s_%(method_name)s_Ordinal);
  $encoder.alloc(%(size)s - $fidl_kMessageHeaderSize);
''' % {
                'name': name,
                'proxy_name': proxy_name,
                'method_name': method_name,
                'param_names': ', '.join(param_names),
                'size': method.maybe_request_size
            })

        for param, ttname in zip(method.maybe_request, type_tables):
          self.f.write(
              '''\
  _kTT_%(type_table)s.enc($encoder, %(offset)s, %(param_name)s);
''' % {
                  'type_table': ttname,
                  'param_name': _CompileIdentifier(param.name),
                  'offset': param.offset
              })

        self.f.write('''  var $writeResult = $ZxChannelWrite(this.channel,
                                     $encoder.messageData(),
                                     $encoder.messageHandles());
  if ($writeResult !== $ZX_OK) {
    throw "$ZxChannelWrite failed: " + $writeResult;
  }
''')

      if method.has_response:
        type_tables = []
        for param in method.maybe_response:
          type_tables.append(self._CompileType(param.type))
        self.f.write('''
  return $ZxObjectWaitOne(this.channel, $ZX_CHANNEL_READABLE, $ZX_TIME_INFINITE)
      .then(() => new Promise(res => {
          var $readResult = $ZxChannelRead(this.channel);
          if ($readResult.status !== $ZX_OK) {
            throw "channel read failed";
          }

          var $view = new DataView($readResult.data);

          var $decoder = new $fidl_Decoder($view, $readResult.handles);
          $decoder.claimMemory(%(size)s - $fidl_kMessageHeaderSize);
''' % {'size': method.maybe_response_size})
        for param, ttname in zip(method.maybe_response, type_tables):
          self.f.write(
              '''\
          var %(param_name)s = _kTT_%(type_table)s.dec($decoder, %(offset)s);
''' % {
                  'type_table': ttname,
                  'param_name': _CompileIdentifier(param.name),
                  'offset': param.offset
              })

        self.f.write('''
          res(%(args)s);
        }));
''' % {'args': ', '.join(x.name for x in method.maybe_response)})

      self.f.write('''};

''')

  def _CompileInterface(self, interface):
    compound = _ParseCompoundIdentifier(interface.name)
    name = _CompileCompoundIdentifier(compound)
    self._GenerateJsInterfaceForInterface(name, interface)
    self._GenerateJsProxyForInterface(name, interface)


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('json')
  parser.add_argument('--output', required=True)
  args = parser.parse_args()

  fidl_obj = fidl.fidl_from_dict(json.load(open(args.json, 'r')))
  with open(args.output, 'w') as f:
    c = Compiler(fidl_obj, f)
    c.Compile()


if __name__ == '__main__':
  main()
