import React from 'react';
import { createRoot } from 'react-dom/client';

import App from './App';
import './index.css';
import { ready } from './lib/telegram';
import { applyThemeAttribute, initialTheme } from './lib/themes';

// Apply the theme unconditionally first: `ready()` returns early outside the
// Telegram client, so relying on it alone would leave a plain browser (dev,
// tests, a web-app link opened directly) on whatever the default is.
applyThemeAttribute(initialTheme());

// Tell Telegram we are painted before React mounts, so the client stops its
// own loading indicator at the right moment.
ready();

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
