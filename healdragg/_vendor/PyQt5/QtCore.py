"""Минимальная замена PyQt5.QtCore для чтения/записи tdata Telegram Desktop.

Реализованы только те классы и методы, которые использует opentele:
QDataStream, QByteArray, QBuffer, QIODevice, QFile, QDir, QSysInfo.

QByteArray — изменяемая (bytearray), потому что tdata-код делает resize(),
setRawData(), isNull() и передаёт буфер в hashlib.
"""
from __future__ import annotations

import struct
from enum import IntEnum


class ByteOrder(IntEnum):
    BigEndian = 0
    LittleEndian = 1


class Endian:
    ByteOrder = ByteOrder.LittleEndian


class QSysInfo:
    Endian = Endian


class QByteArray(bytearray):
    def __init__(self, data=b"", size=None):
        if size is not None and not data:
            super().__init__(size)
            return
        if isinstance(data, str):
            data = data.encode()
        if isinstance(data, int):
            data = bytes(data)
        super().__init__(data or b"")

    # Qt-совместимый минимум
    def data(self):
        return bytes(self)

    def size(self):
        return len(self)

    def resize(self, size):
        size = max(0, int(size))
        if size < len(self):
            del self[size:]
        elif size > len(self):
            self.extend(b"\x00" * (size - len(self)))

    def reserve(self, size):
        return None

    def isNull(self):
        return len(self) == 0

    def isEmpty(self):
        return len(self) == 0

    def toBase64(self):
        import base64

        return QByteArray(base64.b64encode(bytes(self)))

    def setRawData(self, data, size=None):
        raw = bytes(data)
        self.clear()
        self.extend(raw[: size if size is not None else None])

    def __add__(self, other):
        return QByteArray(bytes(self) + bytes(other))

    def __getitem__(self, item):
        result = super().__getitem__(item)
        return QByteArray(result) if isinstance(result, bytearray) else result

    def __str__(self):
        return bytes(self).decode("utf-8", "replace")


class QIODevice:
    class OpenModeFlag(IntEnum):
        NotOpen = 0
        ReadOnly = 1
        WriteOnly = 2
        ReadWrite = 3
        Append = 4
        Truncate = 8
        Text = 16
        Unbuffered = 32
        NewOnly = 64
        ExistingOnly = 128

    OpenMode = OpenModeFlag

    def __init__(self, data=b""):
        self._buf = bytearray(bytes(data or b""))
        self._pos = 0
        self._open = False
        self._mode = QIODevice.OpenModeFlag.NotOpen

    def open(self, mode):
        self._open = True
        self._mode = mode
        if mode in (QIODevice.OpenModeFlag.WriteOnly, QIODevice.OpenModeFlag.ReadWrite):
            if not (mode & QIODevice.OpenModeFlag.Append):
                del self._buf[:]
            self._pos = len(self._buf)
        else:
            self._pos = 0
        return True

    def close(self):
        self._open = False
        return True

    def isOpen(self):
        return self._open

    def write(self, data):
        if isinstance(data, str):
            data = data.encode()
        raw = bytes(data)
        if self._pos > len(self._buf):
            self._buf.extend(b"\x00" * (self._pos - len(self._buf)))
        self._buf[self._pos : self._pos + len(raw)] = raw
        self._pos += len(raw)
        return len(raw)

    def read(self, n=-1):
        if n is None or n < 0:
            data = bytes(self._buf[self._pos :])
            self._pos = len(self._buf)
            return QByteArray(data)
        data = bytes(self._buf[self._pos : self._pos + n])
        self._pos += len(data)
        return QByteArray(data)

    def readAll(self):
        return self.read(-1)

    def seek(self, pos):
        self._pos = max(0, int(pos))
        return True

    def pos(self):
        return self._pos

    def size(self):
        return len(self._buf)

    def atEnd(self):
        return self._pos >= len(self._buf)

    def data(self):
        return QByteArray(bytes(self._buf))


class QBuffer:
    """Буфер поверх чужого QByteArray: запись идёт прямо в него."""

    OpenModeFlag = QIODevice.OpenModeFlag
    OpenMode = QIODevice.OpenModeFlag

    def __init__(self, data=None):
        self._qba: QByteArray | None = None
        self._buf = bytearray()
        self._pos = 0
        self._open = False
        if data is not None:
            self.setBuffer(data)

    def setBuffer(self, data):
        if data is None:
            self._qba = None
            self._buf = bytearray()
        else:
            self._qba = data if isinstance(data, QByteArray) else QByteArray(data)
            self._buf = self._qba
        self._pos = 0

    def buffer(self):
        return self._qba

    def open(self, mode):
        self._open = True
        self._mode = mode
        if mode in (QIODevice.OpenModeFlag.WriteOnly, QIODevice.OpenModeFlag.ReadWrite):
            if not (mode & QIODevice.OpenModeFlag.Append):
                del self._buf[:]
            self._pos = len(self._buf)
        else:
            self._pos = 0
        return True

    def close(self):
        self._open = False
        return True

    def isOpen(self):
        return self._open

    def write(self, data):
        if isinstance(data, str):
            data = data.encode()
        raw = bytes(data)
        if self._pos > len(self._buf):
            self._buf.extend(b"\x00" * (self._pos - len(self._buf)))
        self._buf[self._pos : self._pos + len(raw)] = raw
        self._pos += len(raw)
        return len(raw)

    def read(self, n=-1):
        if n is None or n < 0:
            data = bytes(self._buf[self._pos :])
            self._pos = len(self._buf)
            return QByteArray(data)
        data = bytes(self._buf[self._pos : self._pos + n])
        self._pos += len(data)
        return QByteArray(data)

    def readAll(self):
        return self.read(-1)

    def seek(self, pos):
        self._pos = max(0, int(pos))
        return True

    def pos(self):
        return self._pos

    def size(self):
        return len(self._buf)

    def atEnd(self):
        return self._pos >= len(self._buf)

    def data(self):
        return QByteArray(bytes(self._buf))


class QFile(QIODevice):
    def __init__(self, path=""):
        super().__init__(b"")
        self._path = str(path)

    def open(self, mode):
        import os

        writing = mode in (QIODevice.OpenModeFlag.WriteOnly, QIODevice.OpenModeFlag.ReadWrite)
        if writing:
            if mode & QIODevice.OpenModeFlag.Append and os.path.isfile(self._path):
                self._buf = bytearray(open(self._path, "rb").read())
            else:
                self._buf = bytearray()
            self._pos = len(self._buf)
            self._open = True
            self._writing = True
            return True
        if not os.path.isfile(self._path):
            self._open = False
            return False
        self._buf = bytearray(open(self._path, "rb").read())
        self._pos = 0
        self._open = True
        self._writing = False
        return True

    def close(self):
        import os

        if self._open and getattr(self, "_writing", False):
            with open(self._path, "wb") as handle:
                handle.write(bytes(self._buf))
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
        self._open = False
        return True

    def exists(self):
        import os

        return os.path.isfile(self._path)

    def remove(self):
        import os

        try:
            os.remove(self._path)
            return True
        except OSError:
            return False

    def setFileName(self, path):
        self._path = str(path)

    def fileName(self):
        return self._path


class QDir:
    def __init__(self, path="."):
        self._path = str(path)

    def exists(self):
        import os

        return os.path.isdir(self._path)

    def mkpath(self, path):
        import os

        os.makedirs(str(path), exist_ok=True)
        return True

    def filePath(self, name):
        import os

        return os.path.join(self._path, str(name))

    def entryList(self, filters=None):
        import os

        try:
            return sorted(os.listdir(self._path))
        except OSError:
            return []

    def removeRecursively(self):
        import shutil

        shutil.rmtree(self._path, ignore_errors=True)
        return True


class QDataStream:
    class Version(IntEnum):
        Qt_1_0 = 1
        Qt_2_0 = 2
        Qt_2_1 = 3
        Qt_3_0 = 4
        Qt_3_1 = 5
        Qt_3_2 = 6
        Qt_3_3 = 7
        Qt_4_0 = 8
        Qt_4_1 = 9
        Qt_4_2 = 10
        Qt_4_3 = 11
        Qt_4_4 = 12
        Qt_4_5 = 13
        Qt_4_6 = 14
        Qt_4_7 = 15
        Qt_4_8 = 16
        Qt_4_9 = 17
        Qt_5_0 = 18
        Qt_5_1 = 19

    ByteOrder = ByteOrder
    BigEndian = ByteOrder.BigEndian
    LittleEndian = ByteOrder.LittleEndian

    def __init__(self, device=None):
        self._device = device if device is not None else QBuffer(QByteArray())
        self._version = QDataStream.Version.Qt_5_1
        self._order = ByteOrder.LittleEndian

    def setVersion(self, version):
        self._version = version

    def version(self):
        return self._version

    def setByteOrder(self, order):
        self._order = order

    def byteOrder(self):
        return self._order

    def setDevice(self, device):
        self._device = device

    def device(self):
        return self._device

    def _prefix(self):
        return "<" if self._order == ByteOrder.LittleEndian else ">"

    def _read(self, fmt, size):
        data = self._device.read(size)
        if len(data) < size:
            raise EOFError("QDataStream: unexpected end of data")
        return struct.unpack(self._prefix() + fmt, bytes(data))[0]

    def _write(self, fmt, value):
        self._device.write(struct.pack(self._prefix() + fmt, value))

    def readInt8(self):
        return self._read("b", 1)

    def readUInt8(self):
        return self._read("B", 1)

    def readInt16(self):
        return self._read("h", 2)

    def readUInt16(self):
        return self._read("H", 2)

    def readInt32(self):
        return self._read("i", 4)

    def readUInt32(self):
        return self._read("I", 4)

    def readInt64(self):
        return self._read("q", 8)

    def readUInt64(self):
        return self._read("Q", 8)

    def readBool(self):
        return bool(self.readUInt8())

    def readRawData(self, size):
        return QByteArray(self._device.read(size))

    def readData(self):
        return self.readRawData(max(0, self._device.size() - self._device.pos()))

    def readBytes(self):
        length = self.readUInt32()
        if length == 0xFFFFFFFF:
            return QByteArray()
        return QByteArray(self._device.read(length))

    def readByteArray(self):
        return self.readBytes()

    def readQString(self):
        length = self.readUInt32()
        if length == 0xFFFFFFFF:
            return ""
        return bytes(self._device.read(length)).decode("utf-16-be", "replace")

    def writeInt8(self, value):
        self._write("b", int(value))

    def writeUInt8(self, value):
        self._write("B", int(value))

    def writeInt16(self, value):
        self._write("h", int(value))

    def writeUInt16(self, value):
        self._write("H", int(value))

    def writeInt32(self, value):
        self._write("i", int(value))

    def writeUInt32(self, value):
        self._write("I", int(value))

    def writeInt64(self, value):
        self._write("q", int(value))

    def writeUInt64(self, value):
        self._write("Q", int(value))

    def writeBool(self, value):
        self.writeUInt8(1 if value else 0)

    def writeRawData(self, data):
        raw = bytes(data)
        self._device.write(raw)
        return len(raw)

    def writeBytes(self, data):
        if data is None:
            self.writeUInt32(0xFFFFFFFF)
            return
        raw = bytes(data)
        if not raw:
            self.writeUInt32(0xFFFFFFFF)
            return
        self.writeUInt32(len(raw))
        self._device.write(raw)

    def writeByteArray(self, data):
        self.writeBytes(data)

    def writeQString(self, value):
        if value is None:
            self.writeUInt32(0xFFFFFFFF)
            return
        raw = str(value).encode("utf-16-be")
        self.writeUInt32(len(raw))
        self._device.write(raw)

    def skipRawData(self, size):
        self._device.read(size)

    def atEnd(self):
        return self._device.atEnd()

    def __lshift__(self, value):
        if isinstance(value, (bytes, bytearray, QByteArray)):
            self.writeBytes(value)
        elif isinstance(value, str):
            self.writeQString(value)
        elif isinstance(value, int):
            self.writeInt32(value)
        else:
            raise TypeError(f"QDataStream << {type(value).__name__}")
        return self

    def __rshift__(self, target):
        if isinstance(target, QByteArray):
            data = self.readBytes()
            target.clear()
            target.extend(bytes(data))
            return self
        if isinstance(target, list):
            target.append(self.readBytes())
            return self
        raise TypeError(f"QDataStream >> {type(target).__name__}")
