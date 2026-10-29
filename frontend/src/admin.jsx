import React from 'react';
import { createRoot } from 'react-dom/client';

import AdminApp from './AdminApp';
import './index.css';
import { applyThemeAttribute, initialTheme } from './lib/themes';

// Paint the saved theme before React mounts: the operator console is opened
// dozens of times a day and a flash of the default palette on each one is
// exactly the kind of cheap-looking detail this change exists to remove.
applyThemeAttribute(initialTheme());

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AdminApp />
  </React.StrictMode>,
);
