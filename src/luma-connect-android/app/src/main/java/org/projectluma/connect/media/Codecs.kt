package org.projectluma.connect.media

import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.media.MediaFormat
import android.os.Build

/**
 * Chooses the video codec for a camera or screen session from the computer's
 * preference list (the codecs it can decode). A hardware encoder always wins
 * over a more preferred codec that would be encoded in software. Android's CDD
 * requires a VP8 encoder, so an ordinary Luma desktop (no H.264 decoder) and any
 * phone always have a codec in common.
 */
object Codecs {
    val MIME = mapOf(
        "av1" to MediaFormat.MIMETYPE_VIDEO_AV1,
        "vp9" to MediaFormat.MIMETYPE_VIDEO_VP9,
        "vp8" to MediaFormat.MIMETYPE_VIDEO_VP8,
        "h264" to MediaFormat.MIMETYPE_VIDEO_AVC,
        "h265" to MediaFormat.MIMETYPE_VIDEO_HEVC,
    )

    data class Choice(val codec: String, val encoder: String)

    fun choose(preferred: List<String>): Choice? {
        val encoders = MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos.filter { it.isEncoder }
        fun candidates(codec: String): List<MediaCodecInfo> {
            val mime = MIME[codec] ?: return emptyList()
            return encoders.filter { info ->
                info.supportedTypes.any { it.equals(mime, ignoreCase = true) } &&
                    runCatching {
                        info.getCapabilitiesForType(mime).colorFormats.contains(MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
                    }.getOrDefault(false)
            }
        }
        for (codec in preferred) {
            candidates(codec).firstOrNull { hardware(it) }?.let { return Choice(codec, it.name) }
        }
        for (codec in preferred) {
            candidates(codec).firstOrNull()?.let { return Choice(codec, it.name) }
        }
        return null
    }

    private fun hardware(info: MediaCodecInfo): Boolean =
        if (Build.VERSION.SDK_INT >= 29) info.isHardwareAccelerated
        else !info.name.startsWith("OMX.google.") && !info.name.startsWith("c2.android.")
}
