import React from 'react';
import { createRoot } from 'react-dom/client';
import { Router } from 'react-router-dom';
import { OverridableContext } from 'react-overridable';
import { history, http, InvenioILSApp, SectionServices } from '@inveniosoftware/react-invenio-app-ils';
import 'semantic-ui-less/semantic.less';

// Invenio Accounts accepts form-encoded authentication requests.
const accountForms = new Set([
  '/login', '/register', '/confirm-email', '/forgot-password', '/reset-password',
  '/change-password', '/send-confirmation-email',
]);
http.interceptors.request.use((request) => {
  const path = new URL(request.url, window.location.origin).pathname.replace(/^\/api/, '');
  if (accountForms.has(path) && request.data) {
    request.data = new URLSearchParams(request.data).toString();
    request.headers['Content-Type'] = 'application/x-www-form-urlencoded';
  }
  return request;
});

const config = { APP: {
  INVENIO_UI_URL: window.location.origin,
  REST_ENDPOINTS_BASE_URL: window.location.origin + '/api',
  LOGO_SRC: '/images/logo-invenio-ils.svg',
  ENABLE_LOCAL_ACCOUNT_LOGIN: true,
  ENABLE_OAUTH_LOGIN: false,
  OAUTH_PROVIDERS: {},
  ENVIRONMENTS: [],
}};

const components = {
  'SectionsWrapper.servicesInstallationSections': () => <SectionServices />,
};

createRoot(document.getElementById('app')).render(
  <Router history={history}>
    <OverridableContext.Provider value={components}>
      <InvenioILSApp config={config} />
    </OverridableContext.Provider>
  </Router>
);
