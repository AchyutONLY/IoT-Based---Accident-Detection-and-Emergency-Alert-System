import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.example.accidentdetection',
  appName: 'Accident Detection System',
  webDir: 'out',
  server: {
    cleartext: true,
    androidScheme: "http"
  }
};

export default config;