const path = require('path');

module.exports = {
  plugins: [{ plugin: require('@semantic-ui-react/craco-less') }],
  eslint: { enable: false },
  webpack: {
    configure: (config) => {
      config.resolve.plugins = config.resolve.plugins.filter(
        (plugin) => plugin.constructor.name !== 'ModuleScopePlugin'
      );
      // CRA 5 otherwise emits Axios's .cjs entry as a downloadable asset.
      config.module.rules.find((rule) => rule.oneOf).oneOf.unshift({
        test: /\.cjs$/,
        type: 'javascript/auto',
      });
      return config;
    },
    alias: {
      'axios$': path.resolve(__dirname, 'node_modules/axios/dist/browser/axios.cjs'),
      '@inveniosoftware/react-invenio-app-ils$': path.resolve(
        __dirname, 'node_modules/@inveniosoftware/react-invenio-app-ils/dist/esm/index.js'
      ),
    },
  },
};
