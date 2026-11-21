import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../command_client.dart';

class LiveModeControl extends StatefulWidget {
  const LiveModeControl({super.key, required this.address, required this.role});

  final Uri? address;
  final String role;

  @override
  State<LiveModeControl> createState() => _LiveModeControlState();
}

class _LiveModeControlState extends State<LiveModeControl>
    with WidgetsBindingObserver {
  static const storage = FlutterSecureStorage();
  Timer? timer;
  Map<String, dynamic> snapshot = {};
  bool busy = false;
  bool online = false;

  String get cacheKey => 'liveMemory:${widget.role}:${widget.address}';
  bool get sleeping => snapshot['sleeping'] == true;
  bool get autonomous => snapshot['autonomy'] == true;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    unawaited(_load());
    timer = Timer.periodic(const Duration(seconds: 30), (_) {
      if (WidgetsBinding.instance.lifecycleState == AppLifecycleState.resumed) {
        unawaited(_request());
      }
    });
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) unawaited(_request());
  }

  @override
  void didUpdateWidget(covariant LiveModeControl oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.address != widget.address) {
      snapshot = {};
      online = false;
      unawaited(_load());
    }
  }

  Future<void> _load() async {
    final key = cacheKey;
    try {
      final cached = await storage.read(key: key);
      if (cached != null && mounted && cacheKey == key) {
        final data = jsonDecode(cached);
        if (data is Map<String, dynamic>) setState(() => snapshot = data);
      }
    } catch (_) {
      // The server can restore the journal if the device cache is unavailable.
    }
    if (mounted && cacheKey == key) await _request();
  }

  Future<void> _request([Map<String, dynamic>? change]) async {
    final address = widget.address;
    final key = cacheKey;
    if (address == null || (busy && change == null)) return;
    while (busy) {
      await Future<void>.delayed(const Duration(milliseconds: 100));
      if (!mounted || cacheKey != key) return;
    }
    busy = true;
    final client = CommandClient(address);
    try {
      final data = await client.request(
        change == null ? 'GET' : 'POST',
        '/v1/live/${widget.role}',
        change,
      );
      if (!mounted || cacheKey != key) return;
      final encoded = jsonEncode(data);
      if (encoded != jsonEncode(snapshot)) {
        await storage.write(key: key, value: encoded);
      }
      if (mounted && cacheKey == key) {
        setState(() {
          online = true;
          snapshot = data;
        });
      }
    } catch (_) {
      if (mounted && cacheKey == key) {
        setState(() => online = false);
        if (change != null) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('PC에 연결해 Live 설정을 다시 확인해 주세요.')),
          );
        }
      }
    } finally {
      busy = false;
      client.close();
    }
  }

  Future<void> _journal() async {
    var memory = snapshot['memory'] as String? ?? '';
    final changed = await showDialog<String>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: const Text('나의 기억과 기록'),
        content: SizedBox(
          width: 360,
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                TextFormField(
                  initialValue: memory,
                  onChanged: (value) => memory = value,
                  maxLength: 600,
                  maxLines: 4,
                  decoration: const InputDecoration(labelText: '앞으로도 기억할 내용'),
                ),
                const SizedBox(height: 12),
                const Text('깨어서 완성한 작업'),
                for (final entry in snapshot['projects'] as List? ?? const [])
                  Padding(
                    padding: const EdgeInsets.only(top: 8, bottom: 12),
                    child: SelectableText(entry['text'] as String),
                  ),
                if ((snapshot['projects'] as List? ?? const []).isEmpty)
                  const Text('아직 완료한 자율 작업이 없습니다.'),
                const SizedBox(height: 12),
                const Text('꿈 일기'),
                for (final entry in snapshot['dreams'] as List? ?? const [])
                  Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: SelectableText(entry['text'] as String),
                  ),
                if ((snapshot['dreams'] as List? ?? const []).isEmpty)
                  const Text('아직 기록된 꿈이 없습니다.'),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialog),
            child: const Text('닫기'),
          ),
          FilledButton(
            onPressed: widget.address == null
                ? null
                : () => Navigator.pop(dialog, memory),
            child: const Text('기억 저장'),
          ),
        ],
      ),
    );
    if (changed != null && mounted) await _request({'memory': changed});
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final next = snapshot['next_at'];
    final String state;
    if (!online && widget.address != null) {
      state = 'PC 연결 확인 중';
    } else if (!sleeping && snapshot['working'] == true) {
      state = '자율 작업 중';
    } else if (!sleeping && !autonomous) {
      state = '깨어 있음';
    } else if (snapshot['dreaming'] == true) {
      state = '꿈꾸는 중';
    } else if (next is int) {
      final time = TimeOfDay.fromDateTime(
        DateTime.fromMillisecondsSinceEpoch(next * 1000),
      );
      final activity = sleeping ? '꿈' : '자율 작업';
      state =
          '${snapshot['error'] == true ? '지난 추론 실패 · ' : ''}다음 $activity ${time.format(context)}';
    } else {
      state = '잠자는 중';
    }
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              OutlinedButton.icon(
                onPressed: widget.address == null
                    ? null
                    : () => _request({'sleeping': !sleeping}),
                icon: Icon(
                  sleeping ? Icons.dark_mode : Icons.nightlight_outlined,
                ),
                label: Text(sleeping ? 'Live · 깨우기' : 'Live · 잠자기'),
              ),
              const SizedBox(width: 8),
              Expanded(child: Text(state, overflow: TextOverflow.ellipsis)),
              IconButton(
                onPressed: _journal,
                icon: const Icon(Icons.book_outlined),
                tooltip: '나의 기억과 기록',
              ),
            ],
          ),
          if (!sleeping)
            TextButton.icon(
              onPressed: widget.address == null
                  ? null
                  : () => _request({'autonomy': !autonomous}),
              icon: Icon(autonomous ? Icons.pause : Icons.play_arrow),
              label: Text(autonomous ? '자율 작업 멈추기' : '자율 작업 시작'),
            ),
        ],
      ),
    );
  }
}
