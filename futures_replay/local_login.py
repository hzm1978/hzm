from __future__ import annotations

"""Optional Windows-user-bound login storage, never part of portable state."""

import ctypes
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def _dpapi(data, *, decrypt=False):
    if os.name != "nt":
        raise OSError("此系统没有Windows本机凭据加密服务")
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size",wintypes.DWORD),("data",ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    library = ctypes.WinDLL("crypt32",use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),
                         ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output)):
        raise ctypes.WinError(ctypes.get_last_error())
    free = ctypes.WinDLL("kernel32").LocalFree
    free.argtypes = [ctypes.c_void_p]; free.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(output.data,output.size)
    finally:
        free(ctypes.cast(output.data,ctypes.c_void_p))


class LocalLoginStore:
    def __init__(self, path=None, *, protect=None, unprotect=None):
        base = Path(os.environ.get("LOCALAPPDATA",Path.home()/"AppData"/"Local"))
        self.path = Path(path) if path is not None else base/"FuturesReplay"/"tianqin_login.dpapi"
        self.available = os.name == "nt" or (protect is not None and unprotect is not None)
        self._protect = protect or _dpapi
        self._unprotect = unprotect or (lambda data:_dpapi(data,decrypt=True))

    def load(self):
        if not self.available:
            return None
        try:
            data = json.loads(self._unprotect(self.path.read_bytes()).decode("utf-8"))
            if isinstance(data,dict) and data.get("username") and data.get("password"):
                return str(data["username"]),str(data["password"])
        except (OSError,ValueError,TypeError,UnicodeError):
            pass
        return None

    def save(self,username,password):
        if not self.available:
            raise OSError("本机加密不可用，不能保存密码")
        plain = json.dumps(dict(username=username,password=password),ensure_ascii=False).encode("utf-8")
        encrypted = self._protect(plain)
        if not encrypted or encrypted == plain:
            raise OSError("凭据未加密，已停止保存")
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with NamedTemporaryFile(dir=self.path.parent,suffix=".tmp",delete=False) as file:
            temporary = Path(file.name);file.write(encrypted)
        try:
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def clear(self):
        self.path.unlink(missing_ok=True)


def load_saved_login():
    return LocalLoginStore().load()
