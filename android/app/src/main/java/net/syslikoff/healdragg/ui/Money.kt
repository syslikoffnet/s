package net.syslikoff.healdragg.ui

import org.json.JSONObject
import java.util.Locale
import java.util.Currency
import java.math.BigDecimal

/**
 * Цены в валюте приложения.
 *
 * Telegram отдаёт стоимость подарка в своей валюте (TON, звёзды). Показываем
 * пересчёт в то, что человек выбрал в настройках (по умолчанию рубли), по
 * курсам с сервера; курсы можно поправить там же.
 */
object Money {

    var currency: String = "RUB"
    var usdRub: Double = 92.0
    var tonUsd: Double = 5.0
    var starUsd: Double = 0.013

    fun apply(payload: JSONObject?) {
        if (payload == null) return
        val chosen = payload.optString("currency", currency).uppercase(Locale.US)
        if (chosen.isNotBlank()) currency = chosen
        val rates = payload.optJSONObject("rates") ?: return
        usdRub = rate(rates, "usd_rub", usdRub)
        tonUsd = rate(rates, "ton_usd", tonUsd)
        starUsd = rate(rates, "star_usd", starUsd)
    }

    fun snapshot(): JSONObject = JSONObject()
        .put("currency", currency)
        .put(
            "rates",
            JSONObject()
                .put("usd_rub", usdRub)
                .put("ton_usd", tonUsd)
                .put("star_usd", starUsd),
        )

    private fun rate(rates: JSONObject, key: String, fallback: Double): Double {
        val value = rates.optDouble(key, fallback)
        return if (value.isFinite() && value > 0) value else fallback
    }

    private fun toUsd(amount: Double, source: String?): Double? = when (source?.uppercase(Locale.US)) {
        "USD", "USDT", "$" -> amount
        "TON", "GRAM" -> amount * tonUsd
        "STARS", "STAR", "XTR" -> amount * starUsd
        "RUB", "RUR", "₽" -> amount / usdRub
        "EUR", "€" -> amount * 1.08
        else -> null
    }

    /** Цена в валюте приложения: `4 600 ₽`, `250 $`, `13.5 TON`. */
    fun format(amount: Long, source: String?): String? {
        if (amount <= 0) return null
        val usd = toUsd(amount.toDouble(), source) ?: return null
        return when (currency) {
            "USD" -> money(usd) + " $"
            "EUR" -> money(usd / 1.08) + " €"
            "TON" -> money(usd / tonUsd) + " TON"
            "STARS" -> money(usd / starUsd) + " ★"
            else -> money(usd * usdRub) + " ₽"
        }
    }

    /** Оригинальная цена из Telegram + пересчёт: `1 843 TON · 856 000 ₽`. */
    fun both(amount: Long, source: String?): String? {
        val converted = format(amount, source) ?: return null
        if (source.isNullOrBlank() || source.equals(currency, true)) return converted
        val original = "%,d".format(Locale.US, amount).replace(',', ' ') + " " + source.uppercase(Locale.US)
        return "$original · $converted"
    }

    /** Оценка Telegram: фиат в минимальных единицах, USD — в центах.
     * Не считаем исходную валюту звёздами и не выдаём оценку за цену продажи. */
    fun estimate(amount: Long, source: String?, usdCents: Long): String? {
        val code = source?.uppercase(Locale.US)?.trim().orEmpty()
        if (code in setOf("STARS", "STAR", "XTR")) {
            return both(amount, "STARS")?.let { "оценка $it" }
        }
        val digits = try { Currency.getInstance(code).defaultFractionDigits } catch (_: IllegalArgumentException) { -1 }
        val original = when {
            amount <= 0 || code.isBlank() -> null
            digits in 0..4 -> BigDecimal.valueOf(amount, digits).stripTrailingZeros().toPlainString()
                .replace('.', ',') + " " + code
            else -> "$amount мин. ед. $code"
        }
        val usd = when {
            usdCents > 0 -> usdCents.toDouble() / 100.0
            code == "USD" && digits == 2 && amount > 0 -> amount.toDouble() / 100.0
            code == "EUR" && digits == 2 && amount > 0 -> amount.toDouble() / 100.0 * 1.08
            code in setOf("RUB", "RUR") && digits == 2 && amount > 0 -> amount.toDouble() / 100.0 / usdRub
            else -> null
        }
        val converted = usd?.let {
            when (currency) {
                "USD" -> money(it) + " $"
                "EUR" -> money(it / 1.08) + " €"
                "TON" -> money(it / tonUsd) + " TON"
                "STARS" -> money(it / starUsd) + " ★"
                else -> money(it * usdRub) + " ₽"
            }
        }
        val text = when {
            original == null -> converted
            converted == null || code == currency -> original
            else -> "$original · ≈ $converted"
        } ?: return null
        return "оценка $text"
    }

    private fun money(value: Double): String {
        if (value >= 100) {
            val rounded = Math.round(value)
            return "%,d".format(Locale.US, rounded).replace(',', ' ')
        }
        val text = String.format(Locale.US, "%.2f", value).trimEnd('0').trimEnd('.')
        return text.replace('.', ',')
    }
}
