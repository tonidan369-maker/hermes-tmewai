import 'dart:convert';
import 'dart:typed_data';

import 'package:archive/archive.dart';

const int _regularFileMode = 0x81a4; // S_IFREG | 0644
const int _symbolicLinkMode = 0xa1ff; // S_IFLNK | 0777

/// One entry of a ZIP fixture built by [buildUnixZip].
///
/// Unlike `ArchiveFile`, these carry explicit Unix mode bits so entries such as
/// symbolic links are expressed the way real Unix archives express them.
class ZipFixtureEntry {
  const ZipFixtureEntry({
    required this.name,
    required this.bytes,
    required this.unixMode,
  });

  factory ZipFixtureEntry.file(String name, String content) => ZipFixtureEntry(
    name: name,
    bytes: Uint8List.fromList(utf8.encode(content)),
    unixMode: _regularFileMode,
  );

  /// A symlink entry whose content is the link target, as `ln -s` archives it.
  factory ZipFixtureEntry.symlink(String name, String target) =>
      ZipFixtureEntry(
        name: name,
        bytes: Uint8List.fromList(utf8.encode(target)),
        unixMode: _symbolicLinkMode,
      );

  final String name;
  final Uint8List bytes;
  final int unixMode;
}

/// Builds an uncompressed (store method) ZIP with Unix "version made by" and
/// external file attributes.
///
/// `ZipEncoder` always stamps its own external attributes, so it cannot encode
/// a symlink: it would be read back as a regular file. Fixtures that need
/// `ArchiveFile.isSymbolicLink` to be true after decoding are written here.
Uint8List buildUnixZip(List<ZipFixtureEntry> entries) {
  final local = BytesBuilder();
  final central = BytesBuilder();
  var offset = 0;

  for (final entry in entries) {
    final name = utf8.encode(entry.name);
    // General-purpose bit 11 declares that the name is UTF-8, so a conforming
    // reader does not decode non-ASCII names as CP437.
    final flags = entry.name.codeUnits.any((unit) => unit > 0x7f) ? 0x0800 : 0;
    final crc = getCrc32(entry.bytes);

    // Local file header: sig, version needed, flags, method, time, date, crc,
    // compressed size, uncompressed size, name length, extra length.
    final header = _Writer()
      ..u32(0x04034b50)
      ..u16(20)
      ..u16(flags)
      ..u16(0)
      ..u16(0)
      ..u16(0)
      ..u32(crc)
      ..u32(entry.bytes.length)
      ..u32(entry.bytes.length)
      ..u16(name.length)
      ..u16(0)
      ..bytes(name);
    final headerBytes = header.toBytes();
    local
      ..add(headerBytes)
      ..add(entry.bytes);

    // Central directory header: version made by 0x0314 = Unix (3) system.
    final directory = _Writer()
      ..u32(0x02014b50)
      ..u16(0x0314)
      ..u16(20)
      ..u16(flags)
      ..u16(0)
      ..u16(0)
      ..u16(0)
      ..u32(crc)
      ..u32(entry.bytes.length)
      ..u32(entry.bytes.length)
      ..u16(name.length)
      ..u16(0)
      ..u16(0)
      ..u16(0)
      ..u16(0)
      ..u32(entry.unixMode << 16)
      ..u32(offset)
      ..bytes(name);

    central.add(directory.toBytes());
    offset += headerBytes.length + entry.bytes.length;
  }

  final centralBytes = central.toBytes();
  final out = _Writer()
    ..bytes(local.toBytes())
    ..bytes(centralBytes)
    // End Of Central Directory.
    ..u32(0x06054b50)
    ..u16(0)
    ..u16(0)
    ..u16(entries.length)
    ..u16(entries.length)
    ..u32(centralBytes.length)
    ..u32(offset)
    ..u16(0);
  return out.toBytes();
}

class _Writer {
  final BytesBuilder _builder = BytesBuilder();

  void u16(int value) => _builder.add([value & 0xff, (value >> 8) & 0xff]);

  void u32(int value) => _builder.add([
    value & 0xff,
    (value >> 8) & 0xff,
    (value >> 16) & 0xff,
    (value >> 24) & 0xff,
  ]);

  void bytes(List<int> value) => _builder.add(value);

  Uint8List toBytes() => _builder.toBytes();
}
