import 'dart:async';

// ignore: deprecated_member_use
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// 기기에서 인식한 메모를 확인한다.
class MemoPage extends StatefulWidget {
  const MemoPage({
    super.key,
    this.channel = const MethodChannel('mynuri/voice_wait'),
  });
  final MethodChannel channel;

  @override
  State<MemoPage> createState() => _MemoPageState();
}

class _MemoPageState extends State<MemoPage> {
  List<Map<String, dynamic>>? memos;
  String? error;
  bool loading = false;
  Timer? timer;

  @override
  void initState() {
    super.initState();
    _load();
    timer = Timer.periodic(const Duration(seconds: 3), (_) => _load());
  }

  Future<void> _load() async {
    if (loading) return;
    loading = true;
    try {
      final values = await widget.channel.invokeListMethod<dynamic>(
        'listMemos',
      );
      final items = (values ?? [])
          .map((value) => Map<String, dynamic>.from(value as Map))
          .toList();
      if (mounted) {
        setState(() {
          memos = items;
          error = null;
        });
      }
    } catch (_) {
      if (mounted) setState(() => error = '메모를 불러오지 못했습니다.');
    } finally {
      loading = false;
    }
  }

  Future<void> _delete(Map<String, dynamic> memo) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('메모 삭제'),
        content: const Text('이 메모를 삭제할까요?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('취소'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('삭제'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      final deleted = await widget.channel.invokeMethod<bool>('deleteMemo', {
        'id': memo['id'],
      });
      if (deleted != true) throw StateError('메모를 찾을 수 없습니다');
      await _load();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('메모를 삭제하지 못했습니다.')));
      }
    }
  }

  String _time(dynamic value) {
    if (value is! int) return '';
    final date = DateTime.fromMillisecondsSinceEpoch(value).toLocal();
    String two(int value) => value.toString().padLeft(2, '0');
    return '${date.year}.${two(date.month)}.${two(date.day)} ${two(date.hour)}:${two(date.minute)}';
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('모바일 메모')),
    body: error != null
        ? Center(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(error!),
                TextButton(onPressed: _load, child: const Text('다시 시도')),
              ],
            ),
          )
        : memos == null
        ? const Center(child: CircularProgressIndicator())
        : memos!.isEmpty
        ? const Center(child: Text('아직 기록된 메모가 없습니다.'))
        : ListView.builder(
            itemCount: memos!.length,
            itemBuilder: (context, index) {
              final memo = memos![index];
              return ListTile(
                title: SelectableText(memo['text']?.toString() ?? ''),
                subtitle: Text(_time(memo['createdAt'])),
                trailing: IconButton(
                  icon: const Icon(Icons.delete_outline),
                  tooltip: '메모 삭제',
                  onPressed: () => _delete(memo),
                ),
              );
            },
          ),
  );
}
