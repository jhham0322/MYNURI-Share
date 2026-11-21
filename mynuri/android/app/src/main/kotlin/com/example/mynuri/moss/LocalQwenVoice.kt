package com.example.mynuri.moss

import android.content.Context
import android.util.Log
import java.io.File
import java.io.BufferedReader
import java.io.BufferedWriter
import java.io.RandomAccessFile
import java.text.BreakIterator
import java.util.Locale
import java.util.concurrent.TimeUnit

internal object LocalQwenVoice {
    private const val tag = "NuriLocalQwen"
    private const val segmentLimit = 40

    private fun chunks(text: String): List<String> {
        val sentences = BreakIterator.getSentenceInstance(Locale.KOREAN).apply { setText(text) }
        val result = mutableListOf<String>()
        val group = StringBuilder()
        var start = sentences.first()
        var end = sentences.next()
        while (end != BreakIterator.DONE) {
            var sentence = text.substring(start, end).trim()
            while (sentence.length > segmentLimit) {
                val hardLimit = sentence.offsetByCodePoints(0, minOf(110, sentence.codePointCount(0, sentence.length)))
                val split = sentence.lastIndexOf(' ', segmentLimit).takeIf { it > 0 }
                    ?: sentence.indexOf(' ', segmentLimit).takeIf { it in 1..hardLimit }
                    ?: hardLimit
                if (group.isNotEmpty()) {
                    result.add(group.toString())
                    group.clear()
                }
                result.add(sentence.substring(0, split))
                sentence = sentence.substring(split).trimStart()
            }
            if (sentence.isNotEmpty()) {
                if (group.isNotEmpty() && group.length + sentence.length + 1 > segmentLimit) {
                    result.add(group.toString())
                    group.clear()
                }
                if (group.isNotEmpty()) group.append(' ')
                group.append(sentence)
            }
            start = end
            end = sentences.next()
        }
        if (group.isNotEmpty()) result.add(group.toString())
        return result
    }

    fun render(context: Context, text: String, active: () -> Boolean, ready: (File) -> Unit) {
        val root = File(context.filesDir, "qwen-icl")
        val talker = File(root, "qwen-talker-0.6b-base-Q4_K_M.gguf")
        val codec = File(root, "qwen-tokenizer-12hz-Q4_K_M.gguf")
        val speaker = File(root, "assistant-reference.spk")
        val codes = File(root, "assistant-reference.rvq")
        val transcript = File(root, "assistant-reference.txt")
        val executable = File(context.applicationInfo.nativeLibraryDir, "libnuri_qwen.so")
        check(listOf(talker, codec, speaker, codes, transcript).all { it.isFile && it.length() > 0 } &&
            executable.canExecute()) { "기기에 Qwen3-TTS 모델과 도우미 ICL 프로필을 설치해 주세요." }
        val parts = chunks(text)
        require(parts.isNotEmpty()) { "낭독할 문장이 없습니다." }
        val diagnostics = File.createTempFile("qwen-log-", ".txt", context.cacheDir)
        val command = listOf(
            executable.absolutePath, "--model", talker.absolutePath, "--codec", codec.absolutePath,
            "--ref-spk", speaker.absolutePath, "--ref-rvq", codes.absolutePath,
            "--ref-text", transcript.absolutePath, "--lang", "Korean",
        )
        var resident: Process? = null
        try {
            resident = try {
                ProcessBuilder(command + "--resident-wav-stateful").apply {
                    environment()["NURI_QWEN_CODEC_MATMUL"] = "1"
                    redirectError(diagnostics)
                }.start()
            } catch (failure: java.io.IOException) {
                Log.w(tag, "Could not start resident Qwen; using individual segments", failure)
                null
            }
            val input = resident?.outputStream?.bufferedWriter(Charsets.UTF_8)
            val status = resident?.inputStream?.bufferedReader(Charsets.UTF_8)
            for ((index, part) in parts.withIndex()) {
                if (!active()) return
                val output = File.createTempFile("nuri-qwen-part-", ".wav", context.cacheDir)
                var handedOff = false
                try {
                    val process = resident
                    if (process != null && input != null && status != null) {
                        try {
                            if (!renderResident(process, input, status, part, output, active)) return
                        } catch (failure: java.io.IOException) {
                            Log.w(tag, "Resident Qwen process failed; restarting synthesis for this segment", failure)
                            process.destroyForcibly()
                            resident = null
                        }
                    }
                    if (resident == null && !renderPart(command, part, output, diagnostics, active)) return
                    filterPcm(output)
                    Log.i(tag, "On-device ICL segment ${index + 1}/${parts.size}")
                    ready(output)
                    handedOff = true
                } finally {
                    if (!handedOff) output.delete()
                }
            }
        } finally {
            resident?.destroyForcibly()
            diagnostics.delete()
        }
    }

    private fun renderResident(
        process: Process,
        input: BufferedWriter,
        status: BufferedReader,
        text: String,
        output: File,
        active: () -> Boolean,
    ): Boolean {
        val limit = frameLimit(text)
        val utterance = text.replace('\n', ' ').replace('\r', ' ')
        for (seed in listOf("31", "107", "77")) {
            if (!active()) return false
            output.delete()
            val started = android.os.SystemClock.elapsedRealtime()
            input.write("${output.absolutePath}\n$limit\n$seed\n$utterance\n")
            input.flush()
            while (active()) {
                if (status.ready()) {
                    when (status.readLine()) {
                        "NURI_WAV_READY" -> {
                            if (output.length() <= 44) throw java.io.IOException("Missing resident WAV")
                            Log.i(tag, "Resident Qwen segment completed in ${android.os.SystemClock.elapsedRealtime() - started} ms")
                            return true
                        }
                        "NURI_WAV_RETRY" -> break
                        "NURI_WAV_ENGINE_ERROR" -> throw java.io.IOException("Resident Qwen codec failed")
                        null -> throw java.io.IOException("Resident Qwen process ended")
                    }
                }
                if (!process.isAlive || android.os.SystemClock.elapsedRealtime() - started > 120_000) {
                    throw java.io.IOException("Resident Qwen process ended or timed out")
                }
                Thread.sleep(25)
            }
        }
        if (!active()) return false
        error("도우미 음성이 정상적으로 끝나지 않아 재생을 중단했습니다.")
    }

    private fun frameLimit(text: String) =
        (18 + text.codePointCount(0, text.length) * 2).coerceIn(32, 250)

    private fun renderPart(
        command: List<String>,
        text: String,
        output: File,
        diagnostics: File,
        active: () -> Boolean,
    ): Boolean {
        val frameLimit = frameLimit(text)
        for (seed in listOf("31", "107", "77")) {
            if (!active()) return false
            output.delete()
            val process = ProcessBuilder(command + listOf(
                "--seed", seed, "--max-new", frameLimit.toString(), "-o", output.absolutePath,
            ))
                .redirectErrorStream(true).redirectOutput(diagnostics).start()
            try {
                process.outputStream.bufferedWriter(Charsets.UTF_8).use { it.write(text) }
                var finished = false
                for (attempt in 0 until 240) {
                    if (!active()) return false
                    if (process.waitFor(500, TimeUnit.MILLISECONDS)) {
                        finished = true
                        break
                    }
                }
                check(finished && process.exitValue() == 0) { "Qwen3 음성 합성에 실패했습니다." }
            } finally {
                if (process.isAlive) process.destroyForcibly()
            }
            val ended = diagnostics.useLines { lines -> lines.any { "EOS at step" in it } }
            if (ended && output.length() > 44) {
                Log.i(tag, "Qwen segment ended normally (seed $seed, limit $frameLimit frames)")
                return true
            }
            Log.w(tag, "Qwen segment did not end normally (seed $seed, limit $frameLimit frames)")
        }
        error("도우미 음성이 정상적으로 끝나지 않아 재생을 중단했습니다.")
    }

    private fun filterPcm(part: File) {
        RandomAccessFile(part, "rw").use { pcm ->
            val header = ByteArray(44)
            pcm.readFully(header)
            val fields = java.nio.ByteBuffer.wrap(header).order(java.nio.ByteOrder.LITTLE_ENDIAN)
            check(String(header, 0, 4, Charsets.US_ASCII) == "RIFF" &&
                String(header, 36, 4, Charsets.US_ASCII) == "data" &&
                fields.getShort(20).toInt() == 1 && fields.getShort(22).toInt() == 1 &&
                fields.getInt(24) == 24000 && fields.getShort(34).toInt() == 16 &&
                fields.getInt(40).toLong() == part.length() - 44 && part.length() > 44
            ) { "Qwen3 음성 파일 형식이 올바르지 않습니다." }
            val samples = ByteArray(fields.getInt(40))
            pcm.readFully(samples)
            val measurement = QwenEdgeTone.suppress(samples)
            if (measurement.windows > 0) {
                val db = 10 * kotlin.math.log10(measurement.before / measurement.after.coerceAtLeast(1.0))
                Log.i(tag, "PCM edge tone reduced ${db.toInt()} dB in ${measurement.windows} windows; ${samples.size / 2} samples retained")
                val residual = QwenEdgeTone.suppress(samples, residualOnly = true)
                if (residual.windows > 0) {
                    val residualDb = 10 * kotlin.math.log10(residual.before / residual.after.coerceAtLeast(1.0))
                    Log.i(tag, "Residual edge tone reduced ${residualDb.toInt()} dB in ${residual.windows} windows")
                }
            }
            pcm.seek(44)
            pcm.write(samples)
        }
    }
}
