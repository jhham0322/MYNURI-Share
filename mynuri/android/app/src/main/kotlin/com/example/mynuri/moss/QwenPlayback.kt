package com.example.mynuri.moss

import android.media.MediaPlayer
import android.os.SystemClock
import android.util.Log
import java.io.File

/** Receives ready files and plays them in generation order on the main thread. */
internal class QwenPlayback(
    private val deleteAfterPlayback: Boolean = true,
    private val done: (String?) -> Unit,
) {
    private val queued = ArrayDeque<File>()
    private val requestedAt = SystemClock.elapsedRealtime()
    private var activePlayer: MediaPlayer? = null
    private var activeFile: File? = null
    private var generationFinished = false
    private var generationError: String? = null
    private var closed = false
    private var completed = 0
    private var drainedAt: Long? = null

    fun offer(file: File) {
        if (closed) {
            if (deleteAfterPlayback) file.delete()
            return
        }
        queued.addLast(file)
        advance()
    }

    fun finishGeneration(error: String?) {
        generationFinished = true
        generationError = error
        advance()
    }

    fun cancel() = close(null)

    private fun advance() {
        if (closed || activePlayer != null) return
        val file = queued.removeFirstOrNull()
        if (file == null) {
            if (generationFinished) {
                Log.i(TAG, "Playback queue finished: $completed segments, synthesisError=${generationError != null}")
                close(generationError)
            }
            return
        }
        val player = MediaPlayer()
        activePlayer = player
        activeFile = file
        try {
            player.setDataSource(file.absolutePath)
            player.setOnCompletionListener {
                if (activePlayer !== it) return@setOnCompletionListener
                releaseCurrent()
                completed++
                if (queued.isEmpty() && !generationFinished) drainedAt = SystemClock.elapsedRealtime()
                advance()
            }
            player.setOnErrorListener { failed, _, _ ->
                if (activePlayer === failed) close("도우미 음성을 재생하지 못했습니다.")
                true
            }
            player.prepare()
            player.start()
            val now = SystemClock.elapsedRealtime()
            val gap = drainedAt?.let { now - it } ?: 0
            drainedAt = null
            Log.i(TAG, "Playback segment ${completed + 1} started after ${now - requestedAt} ms; queue gap $gap ms; duration ${player.duration} ms")
        } catch (_: Exception) {
            close("도우미 음성을 재생하지 못했습니다.")
        }
    }

    private fun releaseCurrent() {
        val player = activePlayer
        activePlayer = null
        player?.release()
        if (deleteAfterPlayback) activeFile?.delete()
        activeFile = null
    }

    private fun close(error: String?) {
        if (closed) return
        closed = true
        releaseCurrent()
        if (deleteAfterPlayback) queued.forEach { it.delete() }
        queued.clear()
        done(error)
    }

    companion object {
        private const val TAG = "NuriQwenPlayback"
    }
}
