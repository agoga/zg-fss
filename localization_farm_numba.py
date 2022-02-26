from operator import matmul
import numpy as np
import time
import sys
import datetime
import matplotlib.pyplot as plt
from scipy.linalg import circulant
from scipy.optimize import curve_fit
from scipy import stats
from numpy import linalg as la

import multiprocessing as mp
import os
from numba import jit

@jit(nopython=True)
def custom_circulant(array):
    L=len(array)
    ret=np.zeros((L,L))
    array=np.flip(array)

    for i in range(L):
        tmp=np.roll(array, i+1)
        for j in range(len(tmp)):
            ret[i][j]=tmp[j]
    return ret

@jit(nopython=True)
def custom_diag(array):
    L=len(array)
    ret=np.zeros((L,L))

    for i in range(L):
        ret[i,i]=array[i]
    return ret

@jit(forceobj=True)
def save(par, return_value, avg, name, time):
	now = datetime.datetime.now()
	f = open(str(name),"a")
	line = str(now)+" "+str(par)+" "+str(return_value)+" "+str(avg)+" "+str(time)+"s\n"
	f.write(line)

@jit(nopython=True)
def Create_Transfer_Matrix(coupling_matrix_down, W, t_low, c, L, E, dim):
	# P(t)= c*delta(t-t_h) + (1-c)*delta(t-t_l)
	# fraction=c in Z group
	# Dont use dim==2!
	# W does nothing
	N = L * L
	if dim == 3:
		W_strip = W * (np.random.rand(N) - 0.5)
		# make <\eps_i>=0 exactly
		# W_strip = W_strip - np.mean(W_strip)

		# coupling_up is used to form the matrix that couples the nth strip or bar to the n+1th strip or bar
		coupling_up = []
		for i in range(N):
			# c determines the connectivity (fraction of good links) on the lattice
			if np.random.random() < c:
				coupling_up.append(1.0)  # t_hi
			else:
				coupling_up.append(t_low)

		#coupling_matrix_up = np.diag(coupling_up)
		coupling_matrix_up =custom_diag(coupling_up)
		coupling_up_inv = np.linalg.inv(coupling_matrix_up)
		# generate the intra-strip hamiltonian
		minilist = np.zeros(L)
		minilist[1] = 1
		minilist[-1] = 1

		#offdi = circulant(minilist)
		offdi=custom_circulant(minilist)
		I = np.eye(L)
		inner_strip_matrix = np.kron(np.asarray(offdi), I) + np.kron(I, np.asarray(offdi))  # magic!
		inner_strip_matrix = np.triu(inner_strip_matrix)  # so the energies are symmetric
		# Find the ones
		ones_indices = np.nonzero(inner_strip_matrix)
		#ones_indices = np.array([ones_indices[0].astype(np.int64),ones_indices[1].astype(np.int64)])
		ones_indices_1 = ones_indices[0].astype(np.int64)
		ones_indices_2= ones_indices[1].astype(np.int64)
		#ones_indices = np.array(ones_indices)#adam edit


		# Choose the random indices
		ones_range = len(ones_indices[0])
		for ind in range(ones_range):
			if np.random.rand() > c:
				inner_strip_matrix[ones_indices_1[ind], ones_indices_2[ind]] = t_low

		# Transpose it over
		inner_strip_matrix = inner_strip_matrix + np.transpose(inner_strip_matrix)

		# Now add the diagonal disorder
		#inner_strip_matrix = inner_strip_matrix + np.diag(W_strip)
		inner_strip_matrix = inner_strip_matrix + custom_diag(W_strip)

		#upper_left = np.matmul(coupling_up_inv, np.eye(N)*E-inner_strip_matrix)
		#upper_right = -np.matmul(coupling_up_inv, coupling_matrix_down)
		#upper_left = matmul_left_helper(coupling_up_inv,coupling_matrix_down,inner_strip_matrix,E,N)
		#upper_right = matmul_right_helper(coupling_up_inv,coupling_matrix_down,inner_strip_matrix,E,N)

		upper_left = coupling_up_inv@np.eye(N)*E-inner_strip_matrix
		upper_right = -coupling_up_inv@coupling_matrix_down

		lower_left = np.eye(N)
		lower_right = np.zeros((N, N))

		u=(upper_left, upper_right)
		l=(lower_left, lower_right)
		uf=np.hstack(u)
		lf=np.hstack(l)
		transfer_matrix = np.vstack((uf,lf))

	return [transfer_matrix, coupling_matrix_up]

@jit(forceobj=True)
def doCalc(eps,min_Lz,L,W,t_low,c,E,dim):
	#eps: desired error
	#min_Lz: At least this many slices will be computed, no matter what
	#L, V, W: size, potential, diagonal disorder
	
	#P(t)= c*delta(t-t_hi) + (1-c)*delta(t-t_low)
	#t_hi is fixed at 1
	#c: driving parameter. should be 0<c<1
	#t_low: sets low off diagonal disorder value. Needs to be low enough to be in conducting regime

	#housekeeping

	N=L*L
	
	
	#Performs the actual localization length and conductance calculations
	eps_N=100000000000
	Lz=0 #running length
	n_i=5 #number of steps between orthogonalizations.
	n_i_min=5 #Set n_i_min=n_i if you want to force n_i
	Nr=1000 #number of T matrices to generate Q0 with

	#generate first coupling matrix. If c=1, this is the identity matrix.
	coupling_down=[]
	for i in range(N):
		if np.random.random()<c:
			coupling_down.append(1.0)
		else:
			coupling_down.append(t_low)
	coupling_matrix_down = np.diag(coupling_down)
	#Generate Q0
	Q0=np.random.rand(2*N,N)-0.5
	Q0 = Q0.astype(np.float64)
	Q0, r = np.linalg.qr(Q0)
	
	for i in range(Nr):
		T, coupling_matrix_down =Create_Transfer_Matrix(coupling_matrix_down,W,t_low,c,L,E,dim)
		Q0=np.matmul(T,Q0)
		if i%n_i==0:
			Q0, r = np.linalg.qr(Q0)
	
	Q0, r = np.linalg.qr(Q0)
	
	d_a=np.zeros(N,dtype=np.float64)
	e_a=np.zeros(N,dtype=np.float64)
	
	lya=list()
	glst=list()
	Umat=Q0
	timeit = 0
	cnt =0
	while eps_N>eps or Lz<min_Lz:
		M_ni, coupling_matrix_down = Create_Transfer_Matrix(coupling_matrix_down,W,t_low,c,L,E,dim)
		
		for i in range(n_i):
			T, coupling_matrix_down = Create_Transfer_Matrix(coupling_matrix_down,W,t_low,c,L,E,dim)
			M_ni=np.matmul(M_ni,T)
		Umat=np.matmul(M_ni,Umat)
		Umat, r = np.linalg.qr(Umat)

		w_a_norm = np.abs(np.diagonal(r))
		d_a=d_a+np.log(w_a_norm)
		e_a=e_a+np.square(np.log(w_a_norm))
		
		#D_i.append(1/n_i*np.log(w_a_norm))

		Lz=Lz+n_i
		xi_a=d_a/Lz #these are the lyapunov exponents
		nu_a=e_a/Lz
		eps_a=np.sqrt(nu_a-xi_a**2)
		
		sum_xi=np.sum(xi_a)

		old_xi_a = xi_a[N-1]
		sm_pos = np.where(xi_a>0, xi_a, np.inf).min()
		if old_xi_a !=sm_pos:
			cnt += 1
		lya.append(sm_pos)
		glst.append(np.log(np.sum(1/np.cosh(L*xi_a)**2)))
		#eps_N = eps_a[np.where(xi_a==sm_pos)]

			
		if len(lya)<=1:
			eps_N = 1.0 #avoid problems that occur if n_i is too big on the first go-through
		else:
			eps_N = stats.sem(lya)/np.mean(lya) #standard error

		monitor = False
		if Lz % 2000 == 0 and monitor==True:
			print("L: %d, c: %.3f, E: %.1f"%(L,c,E))
			print("Lz: %d" % Lz)
			print("Avg Smallest LE: %.7f" % np.mean(lya))
			print("Std. Error: %.7f%%" % (stats.sem(lya) / np.mean(lya) * 100))
			print("# of discrepencies: %d"%cnt)
			print("======================================")
			timeit = time.time()

	smallestLEAvg = np.mean(lya)
	
	g=np.exp(np.mean(glst))
	print("Calculation complete:")
	print("L: %d, c: %.3f, E: %.1f" % (L, c, E))
	print("Lz: %d" % Lz)
	print("Avg Smallest LE: %.7f" % np.mean(lya))
	print("Std. Error: %.7f%%" % (stats.sem(lya) / np.mean(lya) * 100))
	print("Std. Dev: %.7f"%np.std(lya))
	print("======================================")
	return np.array([float(smallestLEAvg), np.std(lya) , g],dtype=object) #avg of LEs, standard error of mean, g

def main():
	num_processes = 1
	num_args = len(sys.argv)

	#determining a local or server run only by number of args, so don't run this file on farm without arguments
	if num_args == 11:
		eps=float(sys.argv[1])
		min_Lz=float(sys.argv[2])
		L=int(sys.argv[3])
		W=float(sys.argv[4])
		t_low=float(sys.argv[5])
		c=float(sys.argv[6])
		E=float(sys.argv[7])
		dim=int(sys.argv[8])
		num_meas=int(sys.argv[9])
		name=sys.argv[10]
		num_processes=1
	else:
		eps=1
		min_Lz=500000
		L=4
		W=10
		t_low=.3
		c=.5
		E=0
		dim=3
		num_meas=1#number of realizations requested
		name="local_test.txt"

	crange = np.linspace(0.35,0.45, 11)
	Lrange = np.arange(8, 14)
	params=(eps,min_Lz,L,W,t_low,c,E,dim)
	start = time.time()

	if num_processes > 1:
		
		pool = mp.Pool(processes=num_processes)
		results = pool.starmap(doCalc, ((params, ) * num_meas))
		results = np.squeeze(results)
	else:
		results = np.array([doCalc(*params) for x in range(num_meas)],dtype=object) #do the calculation and the averaging
		#results=np.array([np.mean(B[:,0]),np.mean(B[:,1])],dtype=object) #avg lambda, avg g

		#save(params,results,num_meas,filename)

	end = time.time()
	#print("g: %.7f"%ret[2])
	save(params,results,num_meas,name, end-start)


	#if called locally(with no args) give plots
	if num_args <= 1:
		plt.hist(results[:,0], bins='auto')
		plt.xlabel("Lyapunov Exp")
		avgSmPosLE = np.average(results[:,0])
		#SESmPosLE = np.sqrt(np.sum(results[:,1]**2))/(avgSmPosLE * np.sqrt(num_meas))
		SESmPosLE = stats.sem(results[:,0])/avgSmPosLE
		print("Average LE: %.7f" % avgSmPosLE)
		print("Std Err. LE: %.7f" % SESmPosLE)
		print(str(end-start) + " seconds elapsed.")
		plt.show()

if __name__=="__main__":
	np.set_printoptions(linewidth=400)
	#cProfile.run('main()', sort='time')
	main()