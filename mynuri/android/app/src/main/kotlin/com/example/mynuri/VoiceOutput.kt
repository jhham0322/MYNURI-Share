package com.example.mynuri

import android.content.Context
import android.speech.tts.TextToSpeech
import java.util.Locale

object VoiceOutput {
    private var tts: TextToSpeech? = null
    private var ready = false
    private var pending: String? = null

    fun speak(context: Context, text: String) {
        if (ready) {
            tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "nuri-response")
            return
        }
        pending = text
        if (tts != null) return
        tts = TextToSpeech(context.applicationContext) { result ->
            ready = result == TextToSpeech.SUCCESS && (tts?.setLanguage(Locale.KOREAN) ?: TextToSpeech.LANG_NOT_SUPPORTED) >= 0
            if (ready && context.getSharedPreferences("nuri_voice", Context.MODE_PRIVATE).getBoolean("output_enabled", false)) {
                pending?.let { tts?.speak(it, TextToSpeech.QUEUE_FLUSH, null, "nuri-response") }
            }
            pending = null
            if (!ready) { tts?.shutdown(); tts = null }
        }
    }

    fun stop() {
        pending = null
        tts?.stop()
    }
}
