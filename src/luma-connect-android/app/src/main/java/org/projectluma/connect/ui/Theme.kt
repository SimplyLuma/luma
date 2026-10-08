package org.projectluma.connect.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Luma design tokens (config/shared/design-tokens.json) expressed for Compose. */
object Luma {
    val accentLight = Color(0xFF4878B8)
    val accentDark = Color(0xFF5E91D2)
    val green = Color(0xFF3B8967)
    val amber = Color(0xFFB17A3C)
    val destructive = Color(0xFFB75048)
}

private val light = lightColorScheme(
    primary = Luma.accentLight, onPrimary = Color.White,
    background = Color(0xFFF1F2F3), onBackground = Color(0xFF1F2937),
    surface = Color(0xFFFFFFFF), onSurface = Color(0xFF1F2937),
    surfaceVariant = Color(0xFFE8EAEE), onSurfaceVariant = Color(0xFF68717A),
    outline = Color(0x1A273038), error = Luma.destructive,
)

private val dark = darkColorScheme(
    primary = Luma.accentDark, onPrimary = Color(0xFF0E1A2A),
    background = Color(0xFF21252B), onBackground = Color(0xFFE4E8EA),
    surface = Color(0xFF2A2E34), onSurface = Color(0xFFE4E8EA),
    surfaceVariant = Color(0xFF363C44), onSurfaceVariant = Color(0xFF9AA3AB),
    outline = Color(0x17FFFFFF), error = Color(0xFFE97870),
)

@Composable
fun LumaTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (isSystemInDarkTheme()) dark else light,
        shapes = Shapes(small = RoundedCornerShape(10.dp), medium = RoundedCornerShape(10.dp), large = RoundedCornerShape(15.dp)),
        typography = Typography(
            headlineMedium = TextStyle(fontSize = 29.sp, fontWeight = FontWeight(650), lineHeight = 34.sp),
            titleLarge = TextStyle(fontSize = 21.sp, fontWeight = FontWeight.Bold, lineHeight = 26.sp),
            titleMedium = TextStyle(fontSize = 16.sp, fontWeight = FontWeight(675), lineHeight = 22.sp),
            bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 23.sp),
            bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 20.sp),
            labelLarge = TextStyle(fontSize = 15.sp, fontWeight = FontWeight(625)),
            labelMedium = TextStyle(fontSize = 12.sp, fontWeight = FontWeight(550), letterSpacing = 0.4.sp),
        ),
        content = content,
    )
}
