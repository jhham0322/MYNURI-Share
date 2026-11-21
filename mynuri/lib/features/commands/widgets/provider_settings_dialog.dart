// ignore: deprecated_member_use
import 'package:flutter/material.dart';

import '../command_client.dart';

/// PC 누리앱이 사용하는 처리기를 조회하고 저장한다.
class ProviderSettingsDialog extends StatefulWidget {
  const ProviderSettingsDialog({
    super.key,
    required this.address,
    this.clientFactory,
  });

  final Uri address;
  final CommandClient Function(Uri)? clientFactory;

  @override
  State<ProviderSettingsDialog> createState() => _ProviderSettingsDialogState();
}

class _ProviderSettingsDialogState extends State<ProviderSettingsDialog> {
  String? selected;
  String? saved;
  String? error;
  bool loading = false;
  Map<String, String> providers = const {'codex': 'Codex', 'codebuddy-code': 'CodeBuddy'};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      loading = true;
      error = null;
    });
    final client =
        widget.clientFactory?.call(widget.address) ??
        CommandClient(widget.address);
    try {
      final result = await client.request('GET', '/v1/settings/provider');
      final value = result['provider'];
      final advertised = result['providers'];
      final options = advertised is Map ? Map<String, String>.from(advertised) : providers;
      if (value is! String || !options.containsKey(value)) {
        throw const FormatException('알 수 없는 처리기');
      }
      if (mounted) {
        setState(() {
          providers = options;
          selected = value;
          saved = value;
        });
      }
    } catch (_) {
      if (mounted) {
        setState(
          () => error = 'PC에서 처리기 설정을 읽지 못했습니다. PC 연결과 누리앱 버전을 확인해 주세요.',
        );
      }
    } finally {
      client.close();
      if (mounted) setState(() => loading = false);
    }
  }

  Future<void> _save() async {
    if (selected == null || loading) return;
    setState(() {
      loading = true;
      error = null;
    });
    final client =
        widget.clientFactory?.call(widget.address) ??
        CommandClient(widget.address);
    try {
      final result = await client.request('POST', '/v1/settings/provider', {
        'provider': selected,
      });
      if (result['provider'] != selected) {
        throw const FormatException('저장 결과가 다릅니다');
      }
      if (mounted) Navigator.of(context).pop(true);
    } catch (_) {
      if (mounted) {
        setState(() => error = '저장 결과를 확인하지 못했습니다. 설정을 다시 불러와 확인해 주세요.');
      }
    } finally {
      client.close();
      if (mounted) setState(() => loading = false);
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('작업 처리기'),
    content: Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text('PC 누리앱과 같은 설정을 사용합니다. 변경 내용은 다음 작업부터 적용됩니다.'),
        const SizedBox(height: 16),
        if (selected != null && providers.length <= 2)
          SegmentedButton<String>(
            segments: const [
              ButtonSegment(value: 'codex', label: Text('Codex')),
              ButtonSegment(value: 'codebuddy-code', label: Text('CodeBuddy')),
            ],
            selected: {selected!},
            onSelectionChanged: loading
                ? null
                : (values) => setState(() => selected = values.first),
          ),
        if (selected != null && providers.length > 2)
          DropdownButton<String>(
            isExpanded: true,
            value: selected,
            items: [
              for (final provider in providers.entries)
                DropdownMenuItem(value: provider.key, child: Text(provider.value)),
            ],
            onChanged: loading ? null : (value) => setState(() => selected = value),
          ),
        if (loading)
          const Padding(
            padding: EdgeInsets.only(top: 16),
            child: LinearProgressIndicator(),
          ),
        if (error != null) ...[
          const SizedBox(height: 12),
          Text(
            error!,
            style: TextStyle(color: Theme.of(context).colorScheme.error),
          ),
          TextButton(
            onPressed: loading ? null : _load,
            child: const Text('다시 불러오기'),
          ),
        ],
      ],
    ),
    actions: [
      TextButton(
        onPressed: loading ? null : () => Navigator.of(context).pop(),
        child: const Text('닫기'),
      ),
      FilledButton(
        onPressed:
            loading || selected == null || selected == saved || error != null
            ? null
            : _save,
        child: const Text('저장'),
      ),
    ],
  );
}
