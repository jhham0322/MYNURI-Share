package com.example.mynuri.moss

import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Joins canonical 24 kHz mono PCM WAVs without inserting or removing samples. */
internal object QwenReplayWave {
    private const val HEADER_SIZE = 44
    const val FILE_NAME = "answer.wav"

    fun join(directory: File, parts: List<File>): File {
        require(parts.isNotEmpty())
        val temporary = File.createTempFile("answer-", ".part", directory)
        val output = File(directory, FILE_NAME)
        try {
            var dataBytes = 0L
            temporary.outputStream().buffered().use { joined ->
                val header = ByteArray(HEADER_SIZE)
                for ((index, part) in parts.withIndex()) {
                    java.io.DataInputStream(part.inputStream().buffered()).use { source ->
                        source.readFully(header)
                        val fields = ByteBuffer.wrap(header).order(ByteOrder.LITTLE_ENDIAN)
                        val length = fields.getInt(40).toLong()
                        check(String(header, 0, 4, Charsets.US_ASCII) == "RIFF" &&
                            String(header, 8, 8, Charsets.US_ASCII) == "WAVEfmt " &&
                            String(header, 36, 4, Charsets.US_ASCII) == "data" &&
                            fields.getInt(16) == 16 && fields.getShort(20).toInt() == 1 &&
                            fields.getShort(22).toInt() == 1 && fields.getInt(24) == 24000 &&
                            fields.getShort(32).toInt() == 2 && fields.getShort(34).toInt() == 16 &&
                            length > 0 && length % 2 == 0L && length == part.length() - HEADER_SIZE
                        ) { "Invalid Qwen replay WAV" }
                        if (index == 0) joined.write(header)
                        check(source.copyTo(joined) == length)
                        dataBytes += length
                    }
                }
            }
            check(dataBytes <= Int.MAX_VALUE.toLong() - 36)
            RandomAccessFile(temporary, "rw").use { wave ->
                wave.seek(4)
                wave.writeInt(Integer.reverseBytes((dataBytes + 36).toInt()))
                wave.seek(40)
                wave.writeInt(Integer.reverseBytes(dataBytes.toInt()))
            }
            check(temporary.renameTo(output)) { "Could not save combined Qwen WAV" }
            return output
        } finally {
            temporary.delete()
        }
    }
}
