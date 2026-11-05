import { PublicClientApplication } from "@azure/msal-browser";

let client: PublicClientApplication | undefined;

export async function cloudToken(): Promise<string> {
  const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID;
  const tenant = import.meta.env.VITE_ENTRA_TENANT_ID;
  const scope = import.meta.env.VITE_API_SCOPE;
  if (!clientId || !tenant || !scope) throw new Error("Configure the Entra client, tenant, and API scope before signing in.");
  if (!client) {
    client = new PublicClientApplication({
      auth: { clientId, authority: `https://login.microsoftonline.com/${tenant}`, redirectUri: window.location.origin },
      cache: { cacheLocation: "memoryStorage" },
    });
    await client.initialize();
  }
  const result = await client.loginPopup({ scopes: [scope] });
  return result.accessToken;
}
