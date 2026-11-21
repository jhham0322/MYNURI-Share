package com.example.mynuri.moss

import android.content.Context
import android.media.AudioFormat
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.net.Uri
import android.os.SystemClock
import java.nio.ByteOrder
import kotlin.math.sqrt

private const val MAX_REFERENCE_SECONDS = 60

internal fun readVoiceReference(context: Context, uri: Uri): Pair<FloatArray, Int> {
    val extractor = MediaExtractor()
    try {
        extractor.setDataSource(context, uri, null)
        val index = (0 until extractor.trackCount).firstOrNull {
            extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true
        } ?: error("MP3 또는 WAV에서 음성 트랙을 찾지 못했습니다.")
        extractor.selectTrack(index)
        val input = extractor.getTrackFormat(index)
        val mime = requireNotNull(input.getString(MediaFormat.KEY_MIME))
        val decoder = MediaCodec.createDecoderByType(mime)
        try {
            decoder.configure(input, null, null, 0)
            decoder.start()
            var frames = FloatArray(0)
            val info = MediaCodec.BufferInfo()
            var count = 0
            var ended = false
            var output = input
            val deadline = SystemClock.elapsedRealtime() + 120000
            while (SystemClock.elapsedRealtime() < deadline) {
                if (!ended) {
                    val slot = decoder.dequeueInputBuffer(5000)
                    if (slot >= 0) {
                        val buffer = requireNotNull(decoder.getInputBuffer(slot))
                        buffer.clear()
                        val size = extractor.readSampleData(buffer, 0)
                        ended = size < 0
                        decoder.queueInputBuffer(slot, 0, size.coerceAtLeast(0),
                            if (ended) 0 else extractor.sampleTime,
                            if (ended) MediaCodec.BUFFER_FLAG_END_OF_STREAM else 0)
                        if (!ended) extractor.advance()
                    }
                }
                val slot = decoder.dequeueOutputBuffer(info, 5000)
                if (slot == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) output = decoder.outputFormat
                if (slot < 0) continue
                val rate = output.getInteger(MediaFormat.KEY_SAMPLE_RATE)
                val channels = output.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
                require(rate in 8000..192000 && channels in 1..8) { "지원하지 않는 오디오 형식입니다." }
                val limit = rate * MAX_REFERENCE_SECONDS
                if (frames.isEmpty()) frames = FloatArray(limit + 1)
                if (info.size > 0) {
                    val pcm = requireNotNull(decoder.getOutputBuffer(slot)).order(ByteOrder.LITTLE_ENDIAN)
                    pcm.limit(info.offset + info.size)
                    pcm.position(info.offset)
                    val encoding = if (output.containsKey(MediaFormat.KEY_PCM_ENCODING))
                        output.getInteger(MediaFormat.KEY_PCM_ENCODING) else AudioFormat.ENCODING_PCM_16BIT
                    val width = when (encoding) {
                        AudioFormat.ENCODING_PCM_8BIT -> 1
                        AudioFormat.ENCODING_PCM_16BIT -> 2
                        AudioFormat.ENCODING_PCM_FLOAT -> 4
                        else -> error("16비트 PCM WAV 또는 MP3를 사용해 주세요.")
                    }
                    while (pcm.remaining() >= channels * width && count <= limit) {
                        var sample = 0f
                        repeat(channels) {
                            sample += when (encoding) {
                                AudioFormat.ENCODING_PCM_8BIT -> ((pcm.get().toInt() and 255) - 128) / 128f
                                AudioFormat.ENCODING_PCM_FLOAT -> pcm.float
                                else -> pcm.short / 32768f
                            }
                        }
                        frames[count++] = sample / channels
                    }
                }
                decoder.releaseOutputBuffer(slot, false)
                require(count <= limit) { "60초 이하의 녹음을 선택해 주세요. 파일은 자동으로 자르지 않습니다." }
                if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) {
                    require(count >= rate * 2) { "2초 이상 말하는 녹음을 선택해 주세요." }
                    require(sqrt((0 until count).sumOf { frames[it].toDouble() * frames[it] } / count) > 0.001) {
                        "녹음에서 들을 수 있는 음성을 확인해 주세요."
                    }
                    return frames.copyOf(count) to rate
                }
            }
            error("오디오를 읽는 시간이 초과됐습니다.")
        } finally {
            decoder.release()
        }
    } finally {
        extractor.release()
    }
}
