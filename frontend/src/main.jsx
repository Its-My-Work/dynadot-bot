import React from 'react';
import { createRoot } from 'react-dom/client';

import App from './App';
import './index.css';
import { ready } from './lib/telegram';

// Tell Telegram we are painted before React mounts, so the client stops its
// own loading indicator at the right moment.
ready();

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
