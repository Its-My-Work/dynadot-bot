import React from 'react';
import { createRoot } from 'react-dom/client';

import AdminApp from './AdminApp';
import './index.css';

// The admin panel is always dark: it is an operator tool, not a user surface
// that has to match a client theme.
document.documentElement.dataset.theme = 'dark';

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AdminApp />
  </React.StrictMode>,
);
