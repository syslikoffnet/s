package net.syslikoff.healdragg.ui

import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.Dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale

/** Публичная иллюстрация конкретной NFT: модель, фон и узор Fragment. */
@Composable
internal fun GiftArtwork(slug: String?, title: String, size: Dp, fallback: @Composable () -> Unit) {
    val context = LocalContext.current
    val bitmap by produceState<ImageBitmap?>(initialValue = null, key1 = slug) {
        val name = slug?.lowercase(Locale.ROOT)?.takeIf { it.matches(Regex("[a-z0-9]{1,100}-[0-9]{1,20}")) }
            ?: return@produceState
        value = withContext(Dispatchers.IO) {
            runCatching {
                val dir = File(context.cacheDir, "gift-art").apply { mkdirs() }
                val file = File(dir, "$name.jpg")
                if (!file.exists()) {
                    val connection = URL("https://nft.fragment.com/gift/$name.medium.jpg")
                        .openConnection() as HttpURLConnection
                    try {
                        connection.connectTimeout = 8000
                        connection.readTimeout = 8000
                        connection.instanceFollowRedirects = false
                        if (connection.responseCode != 200) return@runCatching null
                        val bytes = connection.inputStream.use { input ->
                            val output = ByteArrayOutputStream()
                            val buffer = ByteArray(8192)
                            while (true) {
                                val count = input.read(buffer)
                                if (count < 0) break
                                if (output.size() + count > 2_000_000) return@runCatching null
                                output.write(buffer, 0, count)
                            }
                            output.toByteArray()
                        }
                        if (bytes.isEmpty()) return@runCatching null
                        val decoded = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return@runCatching null
                        file.writeBytes(bytes)
                        decoded.asImageBitmap()
                    } finally {
                        connection.disconnect()
                    }
                } else {
                    BitmapFactory.decodeFile(file.path)?.asImageBitmap()
                }
            }.getOrNull()
        }
    }
    if (bitmap == null) fallback() else Image(
        bitmap = bitmap!!,
        contentDescription = "Изображение подарка $title",
        contentScale = ContentScale.Crop,
        modifier = Modifier.size(size).clip(RoundedCornerShape(12)),
    )
}

private data class RegularArt(
    val bitmap: ImageBitmap? = null,
    val composition: com.airbnb.lottie.LottieComposition? = null,
)

/** Оригинальный стикер Telegram, по идентификатору подарка, а не по названию. */
@Composable
internal fun RegularGiftArtwork(
    api: Api?, giftId: Long, title: String, size: Dp, fallback: @Composable () -> Unit,
) {
    val art by produceState<RegularArt?>(initialValue = null, key1 = api, key2 = giftId) {
        if (api == null || giftId <= 0) return@produceState
        value = withContext(Dispatchers.IO) {
            runCatching {
                val bytes = api.giftArt(giftId)
                if (bytes.size < 4 || bytes.size > 2_000_000) return@runCatching null
                if (bytes[0] == 0x1f.toByte() && bytes[1] == 0x8b.toByte()) {
                    val json = java.util.zip.GZIPInputStream(bytes.inputStream()).use { stream ->
                        val output = ByteArrayOutputStream()
                        val buffer = ByteArray(8192)
                        while (true) {
                            val n = stream.read(buffer)
                            if (n < 0) break
                            if (output.size() + n > 8_000_000) return@runCatching null
                            output.write(buffer, 0, n)
                        }
                        output.toByteArray()
                    }
                    val composition = com.airbnb.lottie.LottieCompositionFactory
                        .fromJsonInputStreamSync(json.inputStream(), "gift-$giftId").value
                    composition?.let { RegularArt(composition = it) }
                } else {
                    val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, opts)
                    if (opts.outWidth <= 0 || opts.outHeight <= 0 || opts.outWidth > 4096 || opts.outHeight > 4096) {
                        return@runCatching null
                    }
                    val sample = BitmapFactory.Options().apply {
                        inSampleSize = maxOf(1, maxOf(opts.outWidth, opts.outHeight) / 512)
                    }
                    val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, sample)?.asImageBitmap()
                    bitmap?.let { RegularArt(bitmap = it) }
                }
            }.getOrNull()
        }
    }
    when {
        art?.composition != null -> com.airbnb.lottie.compose.LottieAnimation(
            composition = art?.composition,
            progress = { 0.5f },
            modifier = Modifier.size(size),
        )
        art?.bitmap != null -> Image(
            bitmap = art!!.bitmap!!,
            contentDescription = "Изображение подарка $title",
            contentScale = ContentScale.Fit,
            modifier = Modifier.size(size),
        )
        else -> fallback()
    }
}
