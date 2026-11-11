package net.syslikoff.healdragg.ui

import org.junit.Assert.assertEquals
import org.junit.Test

class MoneyTest {
    @Test fun inrMinorUnitsAreNotStars() {
        Money.currency = "USD"
        assertEquals("оценка 1234,56 INR · ≈ 12,34 $", Money.estimate(123456, "INR", 1234))
        assertEquals("оценка 1234,56 INR", Money.estimate(123456, "INR", 0))
    }
    @Test fun fiatMinorUnitsAndMissingCurrency() {
        Money.currency = "USD"
        assertEquals("оценка 10,55 USD", Money.estimate(1055, "USD", 1055))
        assertEquals("оценка 1,23 $", Money.estimate(0, null, 123))
        assertEquals("оценка 12 JPY", Money.estimate(12, "JPY", 0))
        assertEquals("оценка 42 мин. ед. XYZ", Money.estimate(42, "XYZ", 0))
    }
    @Test fun starsRemainStarsAndLargeOriginalDoesNotOverflow() {
        Money.currency = "USD"
        Money.starUsd = 0.013
        assertEquals("оценка 100 STARS · 1,3 $", Money.estimate(100, "STARS", 0))
        assertEquals("5 000 000 000 STARS · 65 000 000 $", Money.both(5_000_000_000L, "STARS"))
    }
}
