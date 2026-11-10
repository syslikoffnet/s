package net.syslikoff.healdragg.ui

import org.json.JSONObject
import java.util.Locale

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
        val original = compactNumber(amount.toInt()) + " " + source.uppercase(Locale.US)
        return "$original · $converted"
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
