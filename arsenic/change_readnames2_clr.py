#!/usr/bin/python

from __future__ import print_function
import sys
import os

#Adds subscripts to reads generated by PBSIM

if len(sys.argv) >= 3:
    fastq_filename = sys.argv[1]
    batchnum = sys.argv[2]
else:
    print("usage: python change_readnames.py fastq_filename batchnum > fastq_out")
    sys.exit(1)

reads=open(fastq_filename,'r') 

lines = reads.readlines()

for i in range(0,len(lines)):
    line = lines[i]
    if i % 2 == 0:
        print (line.rstrip(),'_clr_',batchnum,sep='')
    else:
	print (line.rstrip()),

reads.close()
