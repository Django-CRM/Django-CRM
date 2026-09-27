import 'dart:io';
import 'dart:typed_data';

import 'package:cross_file/cross_file.dart';
import 'package:file_picker/file_picker.dart';

/// A picked file for tests.
///
/// `PlatformFile` is abstract since file_picker 12, and the real subclass is
/// built from a platform-channel map. This mirrors `AndroidPlatformFile`, the
/// one that ships: [size] is what the picker reported (0 means it did not say),
/// and `length()` falls back to measuring [bytes] or the file at [path].
///
/// [size] can disagree with the content on purpose, so a limit test can
/// report 25 MB for a file that holds three bytes.
final class FakePlatformFile extends PlatformFile {
  FakePlatformFile({
    required this.name,
    this.size = 0,
    String? path,
    this.bytes,
  }) : uri = path == null ? Uri(path: name) : Uri.file(path);

  @override
  final String name;

  @override
  final Uri uri;

  final int size;
  final Uint8List? bytes;

  @override
  XFile get xFile => bytes != null
      ? XFile.fromData(bytes!, name: name)
      : XFile(path ?? name, name: name);

  @override
  int? lengthSync() => size > 0 ? size : null;

  @override
  Future<int?> length() async {
    if (size > 0) return size;
    if (bytes != null) return bytes!.length;
    final onDisk = path;
    if (onDisk == null) return null;
    try {
      return await File(onDisk).length();
    } catch (_) {
      return null;
    }
  }

  @override
  Future<Uint8List> readAsBytes() => xFile.readAsBytes();

  @override
  Stream<Uint8List> readAsByteStream() => xFile.openRead();
}
