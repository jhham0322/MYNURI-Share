// ignore: deprecated_member_use
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

class CustomVoiceDialog extends StatefulWidget {
  const CustomVoiceDialog({super.key});

  @override
  State<CustomVoiceDialog> createState() => _CustomVoiceDialogState();
}

class _CustomVoiceDialogState extends State<CustomVoiceDialog> {
  static const _channel = MethodChannel('mynuri/custom_voice');
  bool _busy = true;
  bool _ready = false;
  bool _selected = false;
  bool _hasProfile = false;
  String? _message;

  @override
  void initState() {
    super.initState();
    _run(null);
  }

  Future<void> _run(String? method, {bool enabled = false}) async {
    setState(() {
      _busy = true;
      _message = switch (method) {
        'install' => '모델을 다운로드하는 중입니다. 앱을 열어 두세요.',
        'import' => '선택한 파일에서 목소리를 등록하는 중입니다.',
        'preview' => '미리 듣기 음성을 만드는 중입니다.',
        _ => null,
      };
    });
    try {
      final result = method == null
          ? null
          : await _channel.invokeMethod<Object?>(method, {'enabled': enabled});
      final state = await _channel.invokeMapMethod<String, bool>('status');
      if (!mounted) return;
      setState(() {
        _ready = state?['modelReady'] ?? false;
        _selected = state?['selected'] ?? false;
        _hasProfile = state?['hasProfile'] ?? false;
        _message = method == 'import' && result == true
            ? '등록했습니다. 음성 출력을 켜면 이 목소리로 응답합니다.'
            : null;
      });
    } on PlatformException catch (e) {
      if (mounted) {
        setState(() => _message = e.message ?? '맞춤 음성을 설정하지 못했습니다.');
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: !_busy,
      child: AlertDialog(
        title: const Text('맞춤 목소리'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                _ready
                    ? (_selected ? '등록한 목소리 사용 중' : '기본 목소리 사용 중')
                    : 'MOSS-TTS-Nano 모델을 한 번 내려받습니다(약 763MB).',
              ),
              if (_ready)
                const Text('2~60초 MP3 또는 WAV 전체를 분석해 발화가 많은 연속 구간을 합성에 사용합니다.'),
              if (_busy) const LinearProgressIndicator(),
              if (_message != null) Text(_message!),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: _busy ? null : () => Navigator.pop(context),
            child: const Text('닫기'),
          ),
          if (_hasProfile)
            TextButton(
              onPressed: _busy
                  ? null
                  : () => _run('setEnabled', enabled: !_selected),
              child: Text(_selected ? '기본 목소리' : '등록한 목소리 사용'),
            ),
          if (_hasProfile)
            TextButton(
              onPressed: _busy ? null : () => _run('preview'),
              child: const Text('미리 듣기'),
            ),
          FilledButton(
            onPressed: _busy ? null : () => _run(_ready ? 'import' : 'install'),
            child: Text(_ready ? 'MP3·WAV 가져오기' : '모델 다운로드'),
          ),
        ],
      ),
    );
  }
}
