// craco.config.js
const path = require("path");
require("dotenv").config();

const config = {
  enableHealthCheck: process.env.ENABLE_HEALTH_CHECK === "true",
};

let WebpackHealthPlugin;
let setupHealthEndpoints;
let healthPluginInstance;

if (config.enableHealthCheck) {
  WebpackHealthPlugin = require("./plugins/health-check/webpack-health-plugin");
  setupHealthEndpoints = require("./plugins/health-check/health-endpoints");
  healthPluginInstance = new WebpackHealthPlugin();
}

module.exports = {
  eslint: {
    configure: {
      extends: ["plugin:react-hooks/recommended"],
      rules: {
        "react-hooks/rules-of-hooks": "error",
        "react-hooks/exhaustive-deps": "warn",
      },
    },
  },
  webpack: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
    configure: (webpackConfig) => {
      webpackConfig.watchOptions = {
        ...webpackConfig.watchOptions,
        ignored: [
          "**/node_modules/**",
          "**/.git/**",
          "**/build/**",
          "**/dist/**",
          "**/coverage/**",
          "**/public/**",
        ],
      };
      if (config.enableHealthCheck && healthPluginInstance) {
        webpackConfig.plugins.push(healthPluginInstance);
      }
      return webpackConfig;
    },
  },
  devServer: (devServerConfig) => {
    // Explicit Webpack Dev Server 4-compatible options. This avoids the
    // allowedHosts schema error seen with mixed WDS 4/5 installations.
    devServerConfig.host = "127.0.0.1";
    devServerConfig.port = Number(process.env.PORT || 3000);
    devServerConfig.allowedHosts = ["localhost", "127.0.0.1"];
    devServerConfig.hot = true;
    devServerConfig.historyApiFallback = true;

    // Explicitly proxy API requests to FastAPI. The package.json "proxy"
    // field is not always picked up when CRACO customizes webpack-dev-server,
    // which can cause the browser to request /api/* from port 3002 itself
    // and receive a 404. Keep API calls same-origin in the browser while
    // forwarding them to the FastAPI service on port 8001.
    devServerConfig.proxy = [
      {
        context: ["/api"],
        target: "http://127.0.0.1:8001",
        changeOrigin: true,
        secure: false,
      },
    ];

    if (config.enableHealthCheck && setupHealthEndpoints && healthPluginInstance) {
      const originalSetup = devServerConfig.setupMiddlewares;
      devServerConfig.setupMiddlewares = (middlewares, devServer) => {
        if (typeof originalSetup === "function") {
          middlewares = originalSetup(middlewares, devServer);
        }
        setupHealthEndpoints(devServer, healthPluginInstance);
        return middlewares;
      };
    }

    return devServerConfig;
  },
};
