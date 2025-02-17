from __future__ import print_function
import lldb
import logging
import debugger
import traceback
import ctypes
from ctypes import CFUNCTYPE, POINTER, pointer, sizeof, byref, memmove, c_bool, c_char, c_char_p, c_int, c_int64, c_double, c_size_t, c_void_p
from value import Value

logging.basicConfig(level=logging.DEBUG, #filename='/tmp/codelldb.log',
                    format='%(levelname)s(Python) %(asctime)s %(name)s: %(message)s', datefmt='%H:%M:%S')

log = logging.getLogger('codelldb')

def set_log_level(level):
    logging.getLogger().setLevel(level)

# try:
#     import ptvsd
#     ptvsd.enable_attach(address=('0.0.0.0', 4730))
#     #ptvsd.wait_for_attach()
# except:
#     log.warn('Could not import ptvsd')

#============================================================================================

class SBError(ctypes.Structure):
    _fields_ = [("_opaque", c_int64)]
    swig_type = lldb.SBError

class SBExecutionContext(ctypes.Structure):
    _fields_ = [("_opaque", c_int64 * 2)]
    swig_type = lldb.SBExecutionContext

class SBValue(ctypes.Structure):
    _fields_ = [("_opaque", c_int64 * 2)]
    swig_type = lldb.SBValue

class SBModule(ctypes.Structure):
    _fields_ = [("_opaque", c_int64 * 2)]
    swig_type = lldb.SBModule

class ValueResult(ctypes.Union):
    _fields_ = [('value', SBValue),
                ('error', SBError)]

class BoolResult(ctypes.Union):
    _fields_ = [('value', c_bool),
                ('error', SBError)]

SUCCESS = 1
ERROR = -1

shutdown_cfn = None
evaluate_cfn = None
evaluate_as_bool_cfn = None
modules_loaded_cfn = None
display_html = None

def initialize(init_callback_addr, display_html_addr, callback_context):
    global shutdown_cfn, evaluate_cfn, evaluate_as_bool_cfn, modules_loaded_cfn, display_html
    shutdown_cfn = CFUNCTYPE(c_int)(shutdown)
    evaluate_cfn = CFUNCTYPE(c_int, POINTER(ValueResult), POINTER(c_char), c_size_t, c_bool, SBExecutionContext)(evaluate)
    evaluate_as_bool_cfn = CFUNCTYPE(c_int, POINTER(BoolResult), POINTER(c_char), c_size_t, c_bool, SBExecutionContext)(evaluate_as_bool)
    modules_loaded_cfn = CFUNCTYPE(c_int, POINTER(SBModule), c_size_t)(modules_loaded)

    init_callback_cfn = CFUNCTYPE(None, c_void_p, c_void_p, c_void_p, c_void_p, c_void_p)(init_callback_addr)
    init_callback_cfn(callback_context, shutdown_cfn, evaluate_cfn, evaluate_as_bool_cfn, modules_loaded_cfn)

    display_html_cfn = CFUNCTYPE(None, c_void_p, c_char_p, c_char_p, c_int, c_bool)(display_html_addr)
    display_html = lambda html, title, position, reveal: display_html_cfn(
        callback_context, str_to_bytes(html), str_to_bytes(title), position if position != None else -1, reveal)

def shutdown():
    global shutdown_cfn, evaluate_cfn, evaluate_as_bool_cfn, modules_loaded_cfn, display_html
    display_html = None
    evaluate_cfn = None
    evaluate_as_bool_cfn = None
    modules_loaded_cfn = None
    shutdown_cfn = None
    return SUCCESS

def evaluate(result, expr_ptr, expr_len, is_simple_expr, context):
    try:
        expr = ctypes.string_at(expr_ptr, expr_len)
        context = into_swig_wrapper(context, SBExecutionContext)
        res = evaluate_in_context(expr, is_simple_expr, context)
        res = to_sbvalue(res, context.target)
        result.contents.value = from_swig_wrapper(res, SBValue)
        return SUCCESS
    except Exception as err:
        traceback.print_exc()
        error = lldb.SBError()
        error.SetErrorString(str(err))
        result.contents.error = from_swig_wrapper(error, SBError)
        return ERROR

def evaluate_as_bool(result, expr_ptr, expr_len, is_simple_expr, context):
    try:
        expr = ctypes.string_at(expr_ptr, expr_len)
        context = into_swig_wrapper(context, SBExecutionContext)
        result.contents.value = bool(evaluate_in_context(expr, is_simple_expr, context))
        return SUCCESS
    except Exception as err:
        traceback.print_exc()
        error = lldb.SBError()
        error.SetErrorString(str(err))
        result.contents.error = from_swig_wrapper(error, SBError)
        return ERROR

def modules_loaded(modules_ptr, modules_len):
    try:
        modules = [into_swig_wrapper(modules_ptr[i], SBModule, False) for i in range(modules_len)]
        for module in modules:
            analyze_module(module)
        return SUCCESS
    except Exception as err:
        traceback.print_exc()
        return ERROR

def into_swig_wrapper(cobject, ty, owned=True):
    swig_object = ty.swig_type()
    addr = int(swig_object.this)
    memmove(addr, byref(cobject), sizeof(ty))
    swig_object.this.own(owned)
    return swig_object

def from_swig_wrapper(swig_object, ty):
    swig_object.this.disown() # We'll be moving the value out.
    addr = int(swig_object.this)
    cobject = ty()
    memmove(byref(cobject), addr, sizeof(ty))
    return cobject

sberror = lldb.SBError()

def to_sbvalue(value, target):
    if isinstance(value, lldb.SBValue):
        return value
    elif value is None:
        ty = target.GetBasicType(lldb.eBasicTypeVoid)
        return target.CreateValueFromData('result', lldb.SBData(), ty)
    elif isinstance(value, bool):
        value = c_int(value)
        asbytes = memoryview(value).tobytes()
        data = lldb.SBData()
        data.SetData(sberror, asbytes, target.GetByteOrder(), target.GetAddressByteSize()) # borrows from asbytes
        ty = target.GetBasicType(lldb.eBasicTypeBool)
        return target.CreateValueFromData('result', data, ty)
    elif isinstance(value, int):
        value = c_int64(value)
        asbytes = memoryview(value).tobytes()
        data = lldb.SBData()
        data.SetData(sberror, asbytes, target.GetByteOrder(), target.GetAddressByteSize()) # borrows from asbytes
        ty = target.GetBasicType(lldb.eBasicTypeLongLong)
        return target.CreateValueFromData('result', data, ty)
    elif isinstance(value, float):
        value = c_double(value)
        asbytes = memoryview(value).tobytes()
        data = lldb.SBData()
        data.SetData(sberror, asbytes, target.GetByteOrder(), target.GetAddressByteSize()) # borrows from asbytes
        ty = target.GetBasicType(lldb.eBasicTypeDouble)
        return target.CreateValueFromData('result', data, ty)
    else:
        value = str(value)
        data = lldb.SBData.CreateDataFromCString(target.GetByteOrder(), target.GetAddressByteSize(), value)
        sbtype_arr = target.GetBasicType(lldb.eBasicTypeChar).GetArrayType(len(value))
        return target.CreateValueFromData('result', data, sbtype_arr)

def str_to_bytes(s):
    return s.encode('utf8') if s != None else None

def bytes_to_str(b):
    return b.decode('utf8') if b != None else None

#============================================================================================

def find_var_in_frame(sbframe, name):
    val = sbframe.FindVariable(name)
    if not val.IsValid():
        for val_type in [lldb.eValueTypeVariableGlobal,
                        lldb.eValueTypeVariableStatic,
                        lldb.eValueTypeRegister,
                        lldb.eValueTypeConstResult]:
            val = sbframe.FindValue(name, val_type)
            if val.IsValid():
                break
    if not val.IsValid():
        val = sbframe.GetValueForVariablePath(name)
    return val

# A dictionary-like object that fetches values from SBFrame (and caches them).
class PyEvalContext(dict):
    def __init__(self, sbframe):
        self.sbframe = sbframe

    def __missing__(self, name):
        val = find_var_in_frame(self.sbframe, name)
        if val.IsValid():
            val = Value(val)
            self.__setitem__(name, val)
            return val
        else:
            raise KeyError(name)

def evaluate_in_context(script, simple_expr, execution_context):
    frame = execution_context.GetFrame()
    debugger = execution_context.GetTarget().GetDebugger()
    if simple_expr:
        eval_globals = {}
        eval_locals = PyEvalContext(frame)
        eval_globals['__frame_vars'] = eval_locals
    else:
        import __main__
        eval_globals = getattr(__main__, debugger.GetInstanceName() + '_dict')
        eval_globals['__frame_vars'] = PyEvalContext(frame)
        eval_locals = {}
        lldb.frame = frame
        lldb.thread = frame.GetThread()
        lldb.process = lldb.thread.GetProcess()
        lldb.target = lldb.process.GetTarget()
        lldb.debugger = lldb.target.GetDebugger()
    result = eval(script, eval_globals, eval_locals)
    return Value.unwrap(result)

#============================================================================================

type_callbacks = { None:[] }
type_class_mask_union = 0

# callback: Callable[SBModule]
def register_type_callback(callback, language=None, type_class_mask=lldb.eTypeClassAny):
    global type_callbacks, type_class_mask_union
    type_callbacks.setdefault(language, []).append((type_class_mask, callback))
    type_class_mask_union |= type_class_mask

def analyze_module(sbmodule):
    global type_callbacks, type_class_mask_union
    log.info('### analyzing module %s', sbmodule)
    for cu in sbmodule.compile_units:
        callbacks = type_callbacks.get(None) + type_callbacks.get(cu.GetLanguage(), [])
        types = cu.GetTypes(type_class_mask_union)
        for sbtype in types:
            type_class = sbtype.GetTypeClass()
            for type_class_mask, callback in callbacks:
                if type_class & type_class_mask != 0:
                    try:
                        callback(sbtype)
                    except Exception as err:
                        log.error('Type callback %s raised %s', callback, err)

