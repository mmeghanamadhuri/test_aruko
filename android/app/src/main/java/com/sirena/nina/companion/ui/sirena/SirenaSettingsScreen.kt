package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

/**
 * Mirrors [sirena_ui.screens.settings_screen.SettingsScreen] —
 * category rail (9 entries) + detail stack per category.
 */
@Composable
fun SirenaSettingsScreen(
    selectedCategoryKey: String,
    onCategorySelected: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(modifier.fillMaxSize()) {
        NavigationRail(
            modifier = Modifier.fillMaxHeight(),
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f),
        ) {
            SIRENA_SETTINGS_CATEGORIES.forEach { cat ->
                NavigationRailItem(
                    selected = selectedCategoryKey == cat.key,
                    onClick = { onCategorySelected(cat.key) },
                    icon = { Text(cat.glyph) },
                    label = {
                        Text(
                            cat.label,
                            style = MaterialTheme.typography.labelSmall,
                            maxLines = 2,
                        )
                    },
                )
            }
        }
        Column(
            Modifier
                .weight(1f)
                .fillMaxHeight()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            val cat = SIRENA_SETTINGS_CATEGORIES.find { it.key == selectedCategoryKey }
                ?: SIRENA_SETTINGS_CATEGORIES.first()
            Text(
                "Nina · Settings · ${cat.label}",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(cat.label, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Text(
                "Robot-side settings live on the Jetson; companion network options remain under main Setup. " +
                    "Wire each category to HTTP when exposed on nina-link.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            ) {
                Text(
                    "Details for \"${cat.label}\" — mirror desktop SettingsStack when endpoints exist.",
                    Modifier.padding(16.dp),
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            Text(
                "${SIRENA_SETTINGS_CATEGORIES.size} categories",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
