package com.example.m1crash;

import android.app.Activity;
import android.os.Bundle;

/** Minimal M1 fixture: starting the Activity deterministically crashes the app. */
public final class MainActivity extends Activity {
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        throw new IllegalStateException("M1 deliberate Java crash");
    }
}
