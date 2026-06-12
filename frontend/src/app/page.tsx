import type { Metadata } from 'next';
import DashboardClient from '../components/DashboardClient';

export const metadata: Metadata = {
  title: 'Kor Travel Docker Manager',
  description: 'Kor Travel infrastructure container management and observability control center',
};

export default function Page() {
  return <DashboardClient />;
}
