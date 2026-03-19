import { NextApiRequest, NextApiResponse } from 'next';
import { auth } from '../auth';
import { validateFileUpload } from './validateFileUpload';

const fileUploadRoute = async (req: NextApiRequest, res: NextApiResponse) => {
  const authenticated = await auth(req, res);
  if (!authenticated) {
    return res.status(401).json({ error: 'Unauthorized' });
  }

  if (req.method === 'POST') {
    const file = req.body.file;
    const validationErrors = validateFileUpload(file);
    if (validationErrors.length > 0) {
      return res.status(400).json({ errors: validationErrors });
    }
    // Process file upload
    return res.status(201).json({ message: 'File uploaded successfully' });
  }

  return res.status(405).json({ error: 'Method not allowed' });
};

export default fileUploadRoute;