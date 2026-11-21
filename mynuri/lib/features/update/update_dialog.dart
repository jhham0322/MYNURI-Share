import 'dart:async';
import 'dart:io';

// ignore: deprecated_member_use
import 'package:flutter/material.dart';

import 'update_manifest.dart';

String formatSize(int bytes) =>
    '${(bytes / (1024 * 1024)).toStringAsFixed(1)}MB';

/// 새 버전 안내. 사용자가 업데이트를 선택하면 true를 돌려준다.
Future<bool> confirmUpdate(
  BuildContext context,
  UpdateManifest manifest,
) async {
  final result = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('새 버전이 있습니다'),
      content: Text(
        '${manifest.version} (빌드 ${manifest.build})\n'
        '크기 ${formatSize(manifest.size)}\n\n'
        '지금 업데이트할까요?',
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context, false),
          child: const Text('나중에'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context, true),
          child: const Text('업데이트'),
        ),
      ],
    ),
  );
  return result ?? false;
}

/// 알 수 없는 앱 설치를 허용하지 않은 경우 설정 화면으로 안내한다.
Future<bool> confirmInstallPermission(BuildContext context) async {
  final result = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('설치 권한이 필요합니다'),
      content: const Text('이 앱의 업데이트를 설치하려면 설정에서 "알 수 없는 앱 설치"를 허용해야 합니다.'),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context, false),
          child: const Text('취소'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context, true),
          child: const Text('설정 열기'),
        ),
      ],
    ),
  );
  return result ?? false;
}

/// 다운로드 진행률을 보여 주고, 끝나면 받은 파일을 돌려준다.
class UpdateProgressDialog extends StatefulWidget {
  const UpdateProgressDialog({super.key, required this.download});

  final Future<File> Function(void Function(int received, int total) onProgress)
  download;

  @override
  State<UpdateProgressDialog> createState() => _UpdateProgressDialogState();
}

class _UpdateProgressDialogState extends State<UpdateProgressDialog> {
  double? progress;
  String? error;

  @override
  void initState() {
    super.initState();
    _run();
  }

  Future<void> _run() async {
    try {
      final file = await widget.download((received, total) {
        if (!mounted) return;
        setState(() => progress = total <= 0 ? null : received / total);
      });
      if (mounted) Navigator.of(context).pop(file);
    } catch (e) {
      if (mounted) setState(() => error = _message(e));
    }
  }

  String _message(Object error) {
    if (error is FormatException) return error.message;
    if (error is HttpException) return error.message;
    if (error is TimeoutException || error is SocketException) {
      return '네트워크 연결을 확인해 주세요';
    }
    return '업데이트 파일을 준비하지 못했습니다';
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('업데이트 다운로드'),
    content: error == null
        ? Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              LinearProgressIndicator(value: progress),
              const SizedBox(height: 12),
              Text(
                progress == null
                    ? '준비 중'
                    : '${(progress! * 100).clamp(0, 100).toStringAsFixed(0)}%',
                textAlign: TextAlign.center,
              ),
            ],
          )
        : Text(error!),
    actions: error == null
        ? null
        : [
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('닫기'),
            ),
          ],
  );
}
