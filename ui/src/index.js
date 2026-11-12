import React from 'react';
import { createRoot } from 'react-dom/client';
import { Router } from 'react-router-dom';
import { OverridableContext } from 'react-overridable';
import { history, InvenioILSApp } from '@inveniosoftware/react-invenio-app-ils';
import 'semantic-ui-less/semantic.less';

const config = { APP: {
  INVENIO_UI_URL: window.location.origin,
  REST_ENDPOINTS_BASE_URL: window.location.origin + '/api',
  LOGO_SRC: '/images/logo-invenio-ils.svg',
  ENABLE_LOCAL_ACCOUNT_LOGIN: true,
  ENABLE_OAUTH_LOGIN: false,
  OAUTH_PROVIDERS: {},
  ENVIRONMENTS: [],
}};

createRoot(document.getElementById('app')).render(
  <Router history={history}>
    <OverridableContext.Provider value={{}}>
      <InvenioILSApp config={config} />
    </OverridableContext.Provider>
  </Router>
);
