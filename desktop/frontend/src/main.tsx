import { createRoot } from 'react-dom/client';
import App from './AppV3';
import AppThemeProvider from './AppThemeProvider';

createRoot(document.getElementById('root')!).render(
  <AppThemeProvider>
    <App />
  </AppThemeProvider>,
);
