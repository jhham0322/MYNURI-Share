package com.example.mynuri.moss

import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin

internal object QwenEdgeTone {
    // A 40 ms analysis window at the Qwen codec's 24 kHz output rate.
    private const val window = 960
    private const val hop = window / 2
    private const val edge = 14_400
    private val bins = 37..43 // 925–1075 Hz
    private val hann = DoubleArray(window) { 0.5 - 0.5 * cos(2 * PI * it / window) }
    private val cosine = bins.map { bin -> DoubleArray(window) { cos(2 * PI * bin * it / window) } }
    private val sine = bins.map { bin -> DoubleArray(window) { sin(2 * PI * bin * it / window) } }

    data class Measurement(val windows: Int, val before: Double, val after: Double) {
        operator fun plus(other: Measurement) =
            Measurement(windows + other.windows, before + other.before, after + other.after)
    }

    fun suppress(bytes: ByteArray, residualOnly: Boolean = false): Measurement {
        val pcm = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
        val count = bytes.size / 2
        val edgeLength = minOf(count, edge + window)
        val threshold = if (residualOnly) 0.65 else 0.40
        val fullAttenuation = if (residualOnly) 0.82 else 0.78
        if (count <= 2 * edgeLength) return reduceEdge(pcm, 0, count, threshold, fullAttenuation)
        return reduceEdge(pcm, 0, edgeLength, threshold, fullAttenuation, taperEnd = true) +
            reduceEdge(pcm, count - edgeLength, edgeLength, threshold, fullAttenuation, taperStart = true)
    }

    private fun reduceEdge(
        pcm: ByteBuffer,
        from: Int,
        length: Int,
        threshold: Double,
        fullAttenuation: Double,
        taperStart: Boolean = false,
        taperEnd: Boolean = false,
    ): Measurement {
        val source = DoubleArray(length) { pcm.getShort(2 * (from + it)).toDouble() }
        val removed = DoubleArray(length)
        val weights = DoubleArray(length)
        val real = DoubleArray(bins.count())
        val imaginary = DoubleArray(bins.count())
        val selected = mutableListOf<Int>()
        var before = 0.0
        for (start in -hop until length step hop) {
            val lo = maxOf(0, -start)
            val hi = minOf(window, length - start)
            real.fill(0.0)
            imaginary.fill(0.0)
            var energy = 0.0
            for (index in lo until hi) {
                val weighted = source[start + index] * hann[index]
                energy += weighted * weighted
                for (bin in real.indices) {
                    real[bin] += weighted * cosine[bin][index]
                    imaginary[bin] += weighted * sine[bin][index]
                }
                weights[start + index] += hann[index] * hann[index]
            }
            if (energy <= 1.0) continue
            val band = real.indices.sumOf { real[it] * real[it] + imaginary[it] * imaginary[it] }
            val share = 2.0 * band / (window * energy)
            val attenuation = ((share - threshold) / (fullAttenuation - threshold)).coerceIn(0.0, 1.0) * 0.98
            if (attenuation == 0.0) continue
            selected.add(start)
            before += band
            for (index in lo until hi) {
                val tone = real.indices.sumOf { real[it] * cosine[it][index] + imaginary[it] * sine[it][index] }
                removed[start + index] += 2.0 * tone * hann[index] * attenuation / window
            }
        }
        for (index in source.indices) {
            val distance = when {
                taperStart -> index
                taperEnd -> length - 1 - index
                else -> window
            }
            val blend = hann[minOf(distance / 2, hop)]
            val filtered = source[index] - blend * removed[index] / weights[index].coerceAtLeast(0.5)
            pcm.putShort(2 * (from + index), filtered.roundToInt().coerceIn(-32768, 32767).toShort())
        }
        var after = 0.0
        for (start in selected) {
            val lo = maxOf(0, -start)
            val hi = minOf(window, length - start)
            real.fill(0.0)
            imaginary.fill(0.0)
            for (index in lo until hi) {
                val weighted = pcm.getShort(2 * (from + start + index)) * hann[index]
                for (bin in real.indices) {
                    real[bin] += weighted * cosine[bin][index]
                    imaginary[bin] += weighted * sine[bin][index]
                }
            }
            after += real.indices.sumOf { real[it] * real[it] + imaginary[it] * imaginary[it] }
        }
        return Measurement(selected.size, before, after)
    }
}
