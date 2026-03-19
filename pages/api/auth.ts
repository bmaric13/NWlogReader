```typescript
import type { NextApiRequest, NextApiResponse } from 'next';
import { compare } from 'bcryptjs';
import { sign } from 'jsonwebtoken';

const users = [
  { id: 1, username: 'admin', password: 'password' },
];

const authenticate = async (req: NextApiRequest, res: NextApiResponse) => {
  const { username, password } = req.body;

  const user = users.find((user) => user.username === username);

  if (!user) {
    return res.status(401).json({ message: 'Invalid username or password' });
  }

  const isValidPassword = await compare(password, user.password);

  if (!isValidPassword) {
    return res.status(401).json({ message: 'Invalid username or password' });
  }

  const token = sign({ userId: user.id }, process.env.SECRET_KEY!, {
    expiresIn: '1h',
  });

  res.json({ token });
};

export default authenticate;
```