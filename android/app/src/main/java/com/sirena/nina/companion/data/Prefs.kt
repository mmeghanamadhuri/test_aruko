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
        prefs[Keys.BASE_URL] ?: DEFAULT_BASE_URL
    }

    val bearerToken: Flow<String?> = context.dataStore.data.map { prefs ->
        prefs[Keys.BEARER]
    }

    suspend fun setBaseUrl(url: String) {
        context.dataStore.edit { it[Keys.BASE_URL] = url.trimEnd('/') }
    }

    suspend fun setBearerToken(token: String?) {
        context.dataStore.edit {
            if (token.isNullOrBlank()) it.remove(Keys.BEARER)
            else it[Keys.BEARER] = token.trim()
        }
    }

    companion object {
        const val DEFAULT_BASE_URL = "http://192.168.4.1:8787"
    }
}
