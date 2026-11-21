package com.example.mynuri.moss

import android.content.Context
import android.media.MediaPlayer
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.util.AtomicFile
import android.util.Log
import java.io.File
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.Executors
import javax.net.ssl.HttpsURLConnection
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.abs
import kotlin.math.sqrt

internal object CustomVoice {
    private val worker = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private var player: MediaPlayer? = null
    private var playbackFile: File? = null
    @Volatile private var generation = 0
    private var qwenPlayback: QwenPlayback? = null
    private var cachedPlaybackId: String? = null
    private var recentAnswerIds = emptySet<String>()
    private var replayStorageInitialized = false
    private var engine: MossTTSEngine? = null
    private fun models(context: Context) = File(context.filesDir, "moss")
    private fun profile(context: Context) = AtomicFile(File(context.filesDir, "moss-voice.json"))
    private fun prefs(context: Context) = context.getSharedPreferences("nuri_voice", Context.MODE_PRIVATE)

    fun hasProfile(context: Context) = profile(context).baseFile.isFile

    fun selected(context: Context) = prefs(context).getBoolean("custom_output", false) &&
        hasProfile(context)

    fun modelReady(context: Context) = File(models(context), ".ready-v1").isFile

    fun useCustom(context: Context, enabled: Boolean) {
        require(!enabled || (modelReady(context) && profile(context).baseFile.isFile)) {
            "먼저 모델을 설치하고 목소리를 등록해 주세요."
        }
        prefs(context).edit().putBoolean("custom_output", enabled).apply()
        stop()
    }

    private fun execute(done: (String?) -> Unit, action: () -> Unit) {
        worker.execute {
            val failure = try {
                action()
                null
            } catch (e: Exception) {
                Log.w("NuriCustomVoice", "Custom voice operation failed", e)
                e.localizedMessage ?: "맞춤 음성 작업을 완료하지 못했습니다."
            }
            main.post { done(failure) }
        }
    }

    fun install(context: Context, done: (String?) -> Unit) = execute(done) {
        if (!modelReady(context)) {
            val root = models(context)
            val files = JSONArray(context.assets.open("moss/files.json").bufferedReader().use { it.readText() })
            for (index in 0 until files.length()) {
                val entry = files.getJSONObject(index)
                val target = File(root, entry.getString("path"))
                val size = entry.getLong("size")
                val hash = entry.optString("sha256").takeUnless { it.isBlank() || it == "null" }
                if (target.isFile && target.length() == size) continue
                target.parentFile?.mkdirs()
                val temp = File(target.parentFile, target.name + ".part")
                val connection = URL(entry.getString("url")).openConnection() as HttpsURLConnection
                try {
                    connection.connectTimeout = 15000
                    connection.readTimeout = 45000
                    connection.setRequestProperty("Accept-Encoding", "identity")
                    val digest = MessageDigest.getInstance("SHA-256")
                    var received = 0L
                    connection.inputStream.use { input ->
                        temp.outputStream().buffered().use { output ->
                            val buffer = ByteArray(65536)
                            while (true) {
                                val count = input.read(buffer)
                                if (count < 0) break
                                received += count
                                check(received <= size) { "모델 파일 크기가 다릅니다." }
                                digest.update(buffer, 0, count)
                                output.write(buffer, 0, count)
                            }
                        }
                    }
                    check(received == size && (hash == null ||
                        digest.digest().joinToString("") { "%02x".format(it.toInt() and 255) } == hash)) {
                        "모델 파일 검증에 실패했습니다."
                    }
                    check(temp.renameTo(target)) { "모델 파일을 저장하지 못했습니다." }
                } finally {
                    connection.disconnect()
                    temp.delete()
                }
            }
            val manifest = File(root, "MOSS-TTS-Nano-100M-ONNX/browser_poc_manifest.json")
            context.assets.open("moss/browser_poc_manifest.json").use { input ->
                manifest.outputStream().use { input.copyTo(it) }
            }
            File(root, ".ready-v1").writeText("1")
        }
    }

    private fun getEngine(context: Context): MossTTSEngine {
        check(modelReady(context)) { "MOSS 모델을 먼저 설치해 주세요." }
        return engine ?: MossTTSEngine(models(context), 4).also { engine = it }
    }

    fun import(context: Context, uri: Uri, done: (String?) -> Unit) = execute(done) {
        val (pcm, rate) = readVoiceReference(context, uri)
        val model = getEngine(context)
        // Bound the codec's working memory while retaining every sample in order.
        val segmentSamples = rate * 4
        val codes = buildList {
            for (start in pcm.indices step segmentSamples) {
                val end = (start + segmentSamples).coerceAtMost(pcm.size)
                val samples = pcm.copyOfRange(start, end)
                addAll(model.encodeReferenceAudio(samples, rate))
            }
        }
        check(codes.isNotEmpty()) { "녹음에서 목소리를 등록하지 못했습니다." }
        // Re-encode the prompt in one pass to preserve context across codec frames.
        val prompt = model.encodeReferenceAudio(selectPrompt(pcm, rate), rate)
        fun encodeRows(rows: List<IntArray>) = JSONArray(rows.map { it.toList() })
        val file = profile(context)
        val output = file.startWrite()
        try {
            val saved = JSONObject().put("frames", encodeRows(codes)).put("prompt", encodeRows(prompt))
            output.write(saved.toString().toByteArray(Charsets.UTF_8))
            file.finishWrite(output)
        } catch (e: Exception) {
            file.failWrite(output)
            throw e
        }
        prefs(context).edit().putBoolean("custom_output", true).apply()
    }

    private fun selectPrompt(pcm: FloatArray, rate: Int): FloatArray {
        val hop = rate / 50
        val levels = pcm.asList().indices.step(hop).map { start ->
            val end = minOf(start + hop, pcm.size)
            sqrt((start until end).sumOf { pcm[it].toDouble() * pcm[it] } / (end - start))
        }
        val width = minOf(400, levels.size)
        val threshold = maxOf(0.008, levels.sorted()[levels.size / 2] * 0.45)
        val active = IntArray(levels.size + 1)
        for (index in levels.indices) {
            active[index + 1] = active[index] + if (levels[index] >= threshold) 1 else 0
        }
        val start = (0..levels.size - width).maxByOrNull { index ->
            val end = index + width
            val edgePenalty = (levels[index] + levels[end - 1]).coerceAtMost(0.4) * 75
            active[end] - active[index] - edgePenalty
        } ?: 0
        val samples = pcm.copyOfRange(start * hop, minOf((start + width) * hop, pcm.size))
        val peak = samples.maxOf { abs(it) }
        val gain = minOf(0.9f / peak, 2f)
        val fade = minOf(rate / 50, samples.size / 2)
        for (index in samples.indices) {
            val envelope = minOf(index, samples.lastIndex - index, fade).toFloat() / fade
            samples[index] *= gain * envelope
        }
        return samples
    }

    private fun replayRoot(context: Context) = File(context.noBackupFilesDir, "assistant-replay")

    fun retainAssistantVoice(context: Context, ids: List<String>) {
        recentAnswerIds = ids.takeLast(3).filter { answerIdPattern.matches(it) }.toSet()
        if (cachedPlaybackId?.let { it !in recentAnswerIds } == true) stop()
        replayRoot(context).listFiles()?.forEach { entry ->
            if (answerIdPattern.matches(entry.name) && entry.name !in recentAnswerIds ||
                !replayStorageInitialized && entry.name.startsWith("pending-")) {
                entry.deleteRecursively()
            }
        }
        if (!replayStorageInitialized) {
            context.cacheDir.listFiles()
                ?.filter { it.isFile && legacyOutputPattern.matches(it.name) }
                ?.forEach { it.delete() }
            replayStorageInitialized = true
        }
    }

    private val answerIdPattern = Regex("[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
    private val legacyOutputPattern = Regex("nuri-(?:qwen-part-.+|custom-[0-9]+)\\.wav")

    fun speak(
        context: Context,
        text: String,
        localQwen: Boolean = false,
        answerId: String? = null,
        done: (String?) -> Unit = {},
    ) {
        stop()
        val request = generation
        if (localQwen) {
            val retainedId = answerId?.takeIf { it in recentAnswerIds }
            val destination = retainedId?.let { File(replayRoot(context), it) }
            val combined = destination?.let { File(it, QwenReplayWave.FILE_NAME) }
                ?.takeIf { it.isFile && it.length() > 44 }
            val saved = if (combined == null) destination?.listFiles()
                ?.filter { it.extension == "wav" }
                ?.sortedBy { it.nameWithoutExtension.toIntOrNull() }.orEmpty() else emptyList()
            val legacyReplay = saved.isNotEmpty() && saved.withIndex().all { (index, file) ->
                file.name == "${index + 1}.wav" && file.isFile && file.length() > 44
            }
            val replay = combined != null || legacyReplay
            val playback = QwenPlayback(deleteAfterPlayback = !replay) { error ->
                if (request == generation) {
                    qwenPlayback = null
                    cachedPlaybackId = null
                    generation++
                }
                done(error)
            }
            qwenPlayback = playback
            if (replay) {
                cachedPlaybackId = retainedId
                fun playSaved(files: List<File>) {
                    Log.i("NuriCustomVoice", "Playing ${files.size} saved Assistant WAV file(s)")
                    files.forEach(playback::offer)
                    playback.finishGeneration(null)
                }
                if (combined != null) {
                    playSaved(listOf(combined))
                } else if (destination != null) {
                    var migrated: File? = null
                    execute({ error ->
                        val file = migrated
                        if (file != null) saved.forEach { it.delete() }
                        if (request == generation) {
                            if (error == null && file != null) playSaved(listOf(file))
                            else playSaved(saved)
                        }
                    }) {
                        val wave = File(destination, QwenReplayWave.FILE_NAME)
                        migrated = wave.takeIf { it.isFile } ?: QwenReplayWave.join(destination, saved)
                    }
                }
                return
            }
            val staging = destination?.let { File(it.parentFile, "pending-${it.name}-$request") }
            var cacheReady = false
            execute({ error ->
                if (staging != null && destination != null) {
                    if (error == null && cacheReady && request == generation && retainedId in recentAnswerIds &&
                        destination.deleteRecursively() && staging.renameTo(destination)) {
                        Log.i("NuriCustomVoice", "Saved a single Assistant answer WAV")
                    } else {
                        staging.deleteRecursively()
                    }
                }
                playback.finishGeneration(error)
            }) {
                cacheReady = staging?.mkdirs() == true
                val parts = mutableListOf<File>()
                LocalQwenVoice.render(context, text, { request == generation }) { part ->
                    if (cacheReady && staging != null) {
                        try {
                            parts.add(part.copyTo(File(staging, "${parts.size + 1}.wav")))
                        } catch (error: java.io.IOException) {
                            cacheReady = false
                            Log.w("NuriCustomVoice", "Could not save Assistant audio for replay", error)
                        }
                    }
                    if (!main.post { playback.offer(part) }) part.delete()
                }
                if (cacheReady && staging != null && request == generation) {
                    try {
                        QwenReplayWave.join(staging, parts)
                        parts.forEach { it.delete() }
                    } catch (error: Exception) {
                        cacheReady = false
                        Log.w("NuriCustomVoice", "Could not combine Assistant replay WAV", error)
                    }
                }
            }
            return
        }
        val wav = File(context.cacheDir, "nuri-custom-$request.wav")
        execute({ error ->
            if (request != generation || error != null) {
                wav.delete()
                done(error)
            } else {
                try {
                    val next = MediaPlayer()
                    player = next
                    playbackFile = wav
                    next.setDataSource(wav.absolutePath)
                    next.setOnCompletionListener { if (player === it) stop() }
                    next.setOnErrorListener { media, _, _ ->
                        if (player === media) stop()
                        true
                    }
                    next.prepare()
                    next.start()
                    done(null)
                } catch (e: Exception) {
                    stop()
                    wav.delete()
                    done("합성 음성을 재생하지 못했습니다.")
                }
            }
        }) {
            val raw = String(profile(context).readFully(), Charsets.UTF_8)
            val saved = if (raw.startsWith("{")) JSONObject(raw) else null
            val prompt = saved?.optJSONArray("prompt")
            val json = prompt ?: saved?.getJSONArray("frames") ?: JSONArray(raw)
            val start = if (prompt != null) 0 else saved?.getInt("promptStart") ?: 0
            val count = prompt?.length() ?: saved?.getInt("promptCount") ?: minOf(json.length(), 150)
            require(start >= 0 && count > 0 && start + count <= json.length())
            val codes = List(count) { index ->
                val row = json.getJSONArray(start + index)
                IntArray(row.length()) { row.getInt(it) }
            }
            val model = getEngine(context)
            val audio = model.synthesize(text, referenceAudioCodes = codes)
            check(audio.pcm.isNotEmpty()) { "합성된 오디오가 없습니다." }
            model.writeWav(audio.pcm, wav)
        }
    }

    fun stop() {
        generation++
        qwenPlayback?.cancel()
        qwenPlayback = null
        cachedPlaybackId = null
        player?.release()
        player = null
        playbackFile?.delete()
        playbackFile = null
    }
}
