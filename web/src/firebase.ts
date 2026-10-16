// Firebase for the browser: one app, Firestore (reads only; rules deny every client write) and anonymous Auth.
// With VITE_USE_EMULATORS=true it talks to the local emulators (project argus-local) instead of Google Cloud.
import { initializeApp, type FirebaseApp } from "firebase/app";
import { connectAuthEmulator, getAuth, onAuthStateChanged, signInAnonymously, type Auth, type User } from "firebase/auth";
import { connectFirestoreEmulator, getFirestore, type Firestore } from "firebase/firestore";
import { appEnv } from "./env";

interface Services {
  app: FirebaseApp;
  auth: Auth;
  db: Firestore;
}

let services: Services | undefined;

export function firebase(): Services {
  if (services) return services;
  const env = appEnv();
  const app = initializeApp(env.firebase);
  const auth = getAuth(app);
  const db = getFirestore(app);
  if (env.useEmulators) {
    const [host, port] = env.firestoreEmulatorHost.split(":");
    if (!host || !port || Number.isNaN(Number(port))) {
      throw new Error(`VITE_FIRESTORE_EMULATOR_HOST must be host:port, got "${env.firestoreEmulatorHost}"`);
    }
    connectFirestoreEmulator(db, host, Number(port));
    connectAuthEmulator(auth, env.authEmulatorUrl, { disableWarnings: true });
  }
  services = { app, auth, db };
  return services;
}

export function db(): Firestore {
  return firebase().db;
}

export class AuthError extends Error {
  override name = "AuthError";
}

let signingIn: Promise<User> | undefined;

/** Sign in anonymously (once per page load; Firebase keeps the same anonymous user across reloads). */
export function ensureSignedIn(): Promise<User> {
  if (signingIn) return signingIn;
  const { auth } = firebase();
  signingIn = new Promise<User>((resolve, reject) => {
    const stop = onAuthStateChanged(
      auth,
      (user) => {
        stop();
        if (user) {
          resolve(user);
          return;
        }
        signInAnonymously(auth).then(
          (cred) => {
            resolve(cred.user);
          },
          (e: unknown) => {
            reject(new AuthError(`Anonymous sign-in failed: ${e instanceof Error ? e.message : String(e)}`));
          },
        );
      },
      (e) => {
        stop();
        reject(new AuthError(`Auth state could not be read: ${e.message}`));
      },
    );
  }).catch((e: unknown) => {
    signingIn = undefined; // allow a retry after a failure
    throw e;
  });
  return signingIn;
}

/** A fresh Firebase ID token for `Authorization: Bearer` on API calls. */
export async function getIdToken(): Promise<string> {
  const user = await ensureSignedIn();
  try {
    return await user.getIdToken();
  } catch (e) {
    throw new AuthError(`Could not get an ID token: ${e instanceof Error ? e.message : String(e)}`);
  }
}

/** The signed-in user's uid (owner_uid of the runs they start). */
export async function currentUid(): Promise<string> {
  return (await ensureSignedIn()).uid;
}
