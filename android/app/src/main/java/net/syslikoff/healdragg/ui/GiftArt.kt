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
