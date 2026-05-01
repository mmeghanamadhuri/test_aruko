package com.sirena.nina.companion.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "companion")

class Prefs(private val context: Context) {

    private object Keys {
        val BASE_URL = stringPreferencesKey("base_url")
        val BEARER = stringPreferencesKey("bearer_token")
    }

    val baseUrl: Flow<String> = context.dataStore.data.map { prefs ->
        normalizeBaseUrl(prefs[Keys.BASE_URL] ?: DEFAULT_BASE_URL)
    }

    val bearerToken: Flow<String?> = context.dataStore.data.map { prefs ->
        prefs[Keys.BEARER]
    }

    suspend fun setBaseUrl(url: String) {
        val normalized = normalizeBaseUrl(url)
        context.dataStore.edit { it[Keys.BASE_URL] = normalized }
    }

    suspend fun setBearerToken(token: String?) {
        context.dataStore.edit {
            if (token.isNullOrBlank()) it.remove(Keys.BEARER)
            else it[Keys.BEARER] = token.trim()
        }
    }

    companion object {
        /** NM `wifi hotspot` on Jetson typically uses 10.42.x.x with gateway 10.42.0.1. */
        const val DEFAULT_BASE_URL = "http://10.42.0.1:8787"

        /**
         * Jetson URL must include a scheme and must not start with `/` (OkHttp treats that as a bad host).
         */
        fun normalizeBaseUrl(raw: String): String {
            var s = raw.trim().trimEnd('/')
            if (s.isEmpty()) return DEFAULT_BASE_URL
            s = s.trimStart('/')
            if (!s.startsWith("http://", ignoreCase = true) &&
                !s.startsWith("https://", ignoreCase = true)
            ) {
                s = "http://$s"
            }
            return s.trimEnd('/')
        }
    }
}
