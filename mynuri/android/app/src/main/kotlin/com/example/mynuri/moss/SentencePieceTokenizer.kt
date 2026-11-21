package com.example.mynuri.moss

import java.io.File
import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.text.Normalizer
import kotlin.math.min

/**
 * 轻量级 SentencePiece Unigram 分词器实现。
 * 直接解析 tokenizer.model 的 protobuf 二进制格式，无需 protobuf 运行时。
 */
class SentencePieceTokenizer(modelFile: File) {

    private val pieces: List<Piece>
    private val pieceToId: Map<String, Int>
    private val bosId: Int
    private val eosId: Int
    private val padId: Int
    private val unkId: Int

    data class Piece(
        val text: String,
        val score: Float,
        val type: Int,
    )

    companion object {
        private const val TYPE_NORMAL = 1
        private const val TYPE_UNKNOWN = 2
        private const val TYPE_CONTROL = 3
        private const val TYPE_USER_DEFINED = 4
        private const val TYPE_UNUSED = 5
        private const val TYPE_BYTE = 6

        // Protobuf wire types
        private const val WIRE_VARINT = 0
        private const val WIRE_FIXED64 = 1
        private const val WIRE_LENGTH_DELIMITED = 2
        private const val WIRE_FIXED32 = 5
    }

    init {
        val parsed = try {
            parseModelFile(modelFile.inputStream())
        } catch (e: Exception) {
            throw IllegalStateException(
                "Failed to parse SentencePiece model: ${modelFile.name} (${e.message})", e
            )
        }
        pieces = parsed
        pieceToId = pieces.mapIndexed { index, piece -> piece.text to index }.toMap()

        // 查找特殊 token
        bosId = pieceToId["<s>"] ?: pieceToId["<BOS>"] ?: 1
        eosId = pieceToId["</s>"] ?: pieceToId["<EOS>"] ?: 2
        padId = pieceToId["<pad>"] ?: pieceToId["<PAD>"] ?: 0
        unkId = pieceToId["<unk>"] ?: pieceToId["<UNK>"] ?: 0

        require(pieces.isNotEmpty()) { "Failed to parse SentencePiece model: no pieces found" }
    }

    val vocabSize: Int get() = pieces.size

    fun encode(text: String): IntArray {
        if (text.isEmpty()) return intArrayOf()

        // 预处理：规范化空白
        val normalized = normalize(text)
        if (normalized.isEmpty()) return intArrayOf()

        // Unigram 分词：Viterbi 算法
        return viterbiEncode(normalized)
    }

    fun decode(ids: IntArray): String {
        val sb = StringBuilder()
        val byteBuffer = ArrayList<Byte>()
        fun flushBytes() {
            if (byteBuffer.isNotEmpty()) {
                sb.append(String(byteBuffer.toByteArray(), Charsets.UTF_8))
                byteBuffer.clear()
            }
        }
        for (id in ids) {
            if (id !in pieces.indices) continue
            val piece = pieces[id]
            when (piece.type) {
                TYPE_CONTROL -> continue
                TYPE_BYTE -> {
                    // byte piece 的 text 是单字节码位，需要聚齐后按 UTF-8 解码
                    val code = piece.text.removePrefix("<0x").removeSuffix(">").toIntOrNull(16) ?: -1
                    if (code in 0..255) {
                        byteBuffer += code.toByte()
                    } else {
                        flushBytes()
                        sb.append(piece.text)
                    }
                }
                else -> {
                    flushBytes()
                    // SentencePiece 约定 ▁ 表示空格
                    sb.append(piece.text.replace('▁', ' '))
                }
            }
        }
        flushBytes()
        return sb.toString()
    }

    fun idToPiece(id: Int): String? = pieces.getOrNull(id)?.text

    fun pieceToId(piece: String): Int? = pieceToId[piece]

    private fun normalize(text: String): String {
        if (text.isEmpty()) return text
        // SentencePiece 默认将所有空白替换为 ▁ (U+2581)，折叠连续空白，并在开头补 ▁
        val sb = StringBuilder(text.length + 2)
        sb.append('▁')
        var lastWasSpace = true
        for (c in Normalizer.normalize(text, Normalizer.Form.NFKC).trim()) {
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r' ||
                c == '\u000B' || c == '\u000C' || c == '\u00A0' || c == '\u3000'
            ) {
                if (!lastWasSpace) {
                    sb.append('▁')
                    lastWasSpace = true
                }
            } else {
                sb.append(c)
                lastWasSpace = false
            }
        }
        return sb.toString()
    }

    private fun viterbiEncode(text: String): IntArray {
        val n = text.length
        if (n == 0) return intArrayOf()

        // bestScore[i] = 前 i 个字符的最佳分数
        val bestScore = FloatArray(n + 1) { Float.NEGATIVE_INFINITY }
        val bestPrev = IntArray(n + 1) { -1 }
        val bestPieceId = IntArray(n + 1) { -1 }
        bestScore[0] = 0f
        val unknownScore = pieces.filter { it.type == TYPE_NORMAL }.minOf { it.score } - 10f

        // 预计算所有可能的匹配
        val maxPieceLen = pieces.maxOfOrNull { it.text.length } ?: 16

        for (i in 0 until n) {
            if (bestScore[i] == Float.NEGATIVE_INFINITY) continue
            val maxLen = min(maxPieceLen, n - i)
            val characterLength = Character.charCount(text.codePointAt(i))
            var hasSingleCharacter = false
            for (len in 1..maxLen) {
                val substr = text.substring(i, i + len)
                val id = pieceToId[substr]
                if (id != null) {
                    val piece = pieces[id]
                    if (piece.type == TYPE_NORMAL || piece.type == TYPE_USER_DEFINED) {
                        if (len == characterLength) hasSingleCharacter = true
                        val score = bestScore[i] + piece.score
                        if (score > bestScore[i + len]) {
                            bestScore[i + len] = score
                            bestPrev[i + len] = i
                            bestPieceId[i + len] = id
                        }
                    }
                }
            }
            // 如果没有匹配（或只有特殊类型 token 可匹配），使用 unk（单字符）
            // 注意：长度 1 的普通/byte piece 已在上面 len=1 时匹配过，这里只需处理真正的未登录字符
            val next = i + characterLength
            if (!hasSingleCharacter && bestScore[i] + unknownScore > bestScore[next]) {
                bestScore[next] = bestScore[i] + unknownScore
                bestPrev[next] = i
                bestPieceId[next] = unkId
            }
        }

        // 回溯
        val result = ArrayList<Int>()
        var pos = n
        while (pos > 0) {
            val id = bestPieceId[pos]
            if (id >= 0) {
                if (id == unkId) {
                    val fallback = text.substring(bestPrev[pos], pos).toByteArray(Charsets.UTF_8).map {
                        pieceToId["<0x%02X>".format(it.toInt() and 255)] ?: unkId
                    }
                    result.addAll(0, fallback)
                } else {
                    result.add(0, id)
                }
            }
            pos = bestPrev[pos]
            if (pos < 0) break
        }
        return result.toIntArray()
    }

    /**
     * 手动解析 SentencePiece model protobuf 文件。
     * ModelProto { repeated SentencePiece pieces = 1; ... }
     * SentencePiece { string piece = 1; float score = 2; int32 type = 3; }
     */
    private fun parseModelFile(input: InputStream): List<Piece> {
        val data = input.readBytes()
        val buf = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)
        val pieces = ArrayList<Piece>()

        while (buf.hasRemaining()) {
            val tag = readVarint(buf).toInt()
            val fieldNumber = tag ushr 3
            val wireType = tag and 0x7

            if (fieldNumber == 1 && wireType == WIRE_LENGTH_DELIMITED) {
                // SentencePiece message
                val len = readVarint(buf).toInt()
                if (len < 0 || len > buf.remaining()) error("Malformed SentencePiece entry length: $len")
                val end = buf.position() + len
                parseSentencePiece(buf, end)?.let { pieces.add(it) }
                buf.position(end)
            } else {
                // 跳过其他字段
                skipField(buf, wireType)
            }
        }
        return pieces
    }

    private fun parseSentencePiece(buf: ByteBuffer, end: Int): Piece? {
        var pieceText = ""
        var score = 0f
        var type = TYPE_NORMAL

        while (buf.position() < end) {
            val tag = readVarint(buf).toInt()
            val fieldNumber = tag ushr 3
            val wireType = tag and 0x7

            when (fieldNumber) {
                1 -> { // piece (string)
                    val len = readVarint(buf).toInt()
                    val bytes = ByteArray(len)
                    buf.get(bytes)
                    pieceText = String(bytes, Charsets.UTF_8)
                }
                2 -> { // score (float, fixed32)
                    score = buf.float
                }
                3 -> { // type (enum, varint)
                    type = readVarint(buf).toInt()
                }
                else -> skipField(buf, wireType)
            }
        }
        return Piece(pieceText, score, type)
    }

    private fun readVarint(buf: ByteBuffer): Long {
        var result = 0L
        var shift = 0
        while (shift < 64) {
            if (!buf.hasRemaining()) error("Malformed varint: unexpected end of buffer")
            val b = buf.get().toInt() and 0xFF
            result = result or ((b.toLong() and 0x7FL) shl shift)
            if (b and 0x80 == 0) break
            shift += 7
        }
        return result
    }

    private fun skipField(buf: ByteBuffer, wireType: Int) {
        when (wireType) {
            WIRE_VARINT -> readVarint(buf)
            WIRE_FIXED64 -> buf.position(buf.position() + 8)
            WIRE_LENGTH_DELIMITED -> {
                val len = readVarint(buf).toInt()
                if (len < 0 || len > buf.remaining()) error("Malformed length-delimited field: $len")
                buf.position(buf.position() + len)
            }
            WIRE_FIXED32 -> buf.position(buf.position() + 4)
            else -> error("Unsupported protobuf wire type: $wireType")
        }
    }
}
