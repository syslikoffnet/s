package net.syslikoff.healdragg.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier

@Composable
fun TargetDetailScreen(
    vm: AppViewModel,
    tgId: Long,
    onBack: () -> Unit,
    onToast: (String) -> Unit,
) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text("Цель $tgId", color = Hd.Text)
    }
}
