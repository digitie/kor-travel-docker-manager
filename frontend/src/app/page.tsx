import type { Metadata } from 'next';
import DashboardClient from '../components/DashboardClient';

export const metadata: Metadata = {
  title: 'TripMate Infrastructure Manager',
  description: 'TripMate infrastructure container management and observability control center',
};

export default function Page() {
  return <DashboardClient />;
}
