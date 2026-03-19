```typescript
import NextAuth from 'next-auth';
import Providers from 'next-auth/providers';

export default NextAuth({
  providers: [
    Providers.Credentials({
      name: 'Credentials',
      credentials: {
        username: { label: 'Username', type: 'text' },
        password: { label: 'Password', type: 'password' },
      },
      async authorize(credentials) {
        // Add your authentication logic here
        const user = { id: 1, name: 'J Smith', email: 'jsmith@example.com' };
        return user;
      },
    }),
  ],
  database: process.env.DATABASE_URL,
});
```